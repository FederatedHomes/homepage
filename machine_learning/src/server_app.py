"""pytorchexample: A Flower / PyTorch app."""

import logging
import os
import torch
from flwr.app import ArrayRecord, ConfigRecord, Context, MetricRecord, Message
from flwr.serverapp import Grid, ServerApp
from flwr.serverapp.strategy import FedAvg
from flwr.common import log, logger

import json

from src.task import CustomNet, load_server_data, test_model, validate_model_compatibility
from src.data_contract import CONTRACT

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
CHECKPOINT_DIR = os.environ.get("CHECKPOINT_DIR", "/app/checkpoints")
MODEL_BASE_NAME = os.environ.get("MODEL_BASE_NAME", "final_model.pt")
GLOBAL_MODEL_PREFIX = os.environ.get("GLOBAL_MODEL_PREFIX", "global")
METRICS_BASE_NAME = os.environ.get("METRICS_BASE_NAME", "final_metrics.json")

# Minimum number of successful client responses required to aggregate a round.
# With three registered clients this permits one client failure while preventing
# training from continuing on a single client's update.
MIN_SUCCESSFUL_CLIENTS = 2

# Create ServerApp
app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Main entry point for the ServerApp."""

    # Read run config
    fraction_evaluate: float = context.run_config["fraction-evaluate"]
    num_rounds: int = context.run_config["num-server-rounds"]
    lr: float = context.run_config["learning-rate"]

    # Infer the model shape from the actual data loader so the server model matches the training data.
    test_dataloader = load_server_data(DATA_DIR)

    global_model = CustomNet()
    arrays = ArrayRecord(global_model.state_dict())

    # Initialize FedAvg strategy
    class ContractFedAvg(FedAvg):
        """FedAvg that broadcasts the data contract and handles failed replies."""

        def configure_train(
            self, server_round: int, arrays: ArrayRecord, config: ConfigRecord, grid: Grid
        ) -> list[Message]:
            # Inject the contract version into the per-round config sent to clients
            config["contract-version"] = CONTRACT.version
            return super().configure_train(server_round, arrays, config, grid)

        def aggregate_train(
            self, server_round: int, replies: list[Message]
        ) -> tuple[ArrayRecord | None, MetricRecord | None]:
            """Aggregate only successful, schema-compatible client responses.

            Flower can return a Message without content when a sampled client is
            unavailable or fails during a round. Treat that as a failed client
            instead of dereferencing ``msg.content`` and crashing the ServerApp.
            """
            valid_replies: list[Message] = []
            failed_replies = 0

            for msg in replies:
                if not msg.has_content():
                    failed_replies += 1
                    log(logger, logging.WARNING, "Client reply missing content; excluding it from aggregation")
                    continue

                metrics: MetricRecord = msg.content["metrics"]
                if metrics.get("schema_violation", None):
                    log(logger, logging.WARNING, "Client rejected (schema): %s", metrics["schema_violation"])
                    continue
                valid_replies.append(msg)

            if failed_replies:
                log(
                    logger,
                    logging.WARNING,
                    "Excluded %d failed client response(s) from round %d",
                    failed_replies,
                    server_round,
                )

            if len(valid_replies) < MIN_SUCCESSFUL_CLIENTS:
                raise RuntimeError(
                    f"Round {server_round} aborted: received {len(valid_replies)} successful "
                    f"client response(s), minimum required is {MIN_SUCCESSFUL_CLIENTS}."
                )

            return super().aggregate_train(server_round, valid_replies)

    strategy = ContractFedAvg(fraction_evaluate=fraction_evaluate)

    # Start strategy, run FedAvg for `num_rounds`
    result = strategy.start(
        grid=grid,
        initial_arrays=arrays,
        train_config=ConfigRecord({"lr": lr}),
        num_rounds=num_rounds,
        evaluate_fn=global_evaluate,
    )

    if context.run_config["save-model"]:
        # Save final global metrics
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        final_round = max(result.evaluate_metrics_serverapp.keys())
        final_metrics = dict(result.evaluate_metrics_serverapp.get(final_round, {}))
        metrics_path = os.path.join(
            CHECKPOINT_DIR,
            f"{GLOBAL_MODEL_PREFIX}_{METRICS_BASE_NAME}",
        )
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(final_metrics, f, indent=2)

        # Save final model to disk
        print("\nSaving final model to disk...")
        state_dict = result.arrays.to_torch_state_dict()
        model_path = os.path.join(
            CHECKPOINT_DIR,
            f"{GLOBAL_MODEL_PREFIX}_{MODEL_BASE_NAME}",
        )
        torch.save(state_dict, model_path)


def global_evaluate(server_round: int, arrays: ArrayRecord) -> MetricRecord:
    """Evaluate model on central data."""

    # Load the model and initialize it with the received weights
    test_dataloader = load_server_data(DATA_DIR)

    model = CustomNet()
    state_dict = arrays.to_torch_state_dict()
    validate_model_compatibility(model, state_dict)

    model.load_state_dict(state_dict)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model.to(device)

    # Evaluate the global model on the test set
    test_loss, test_acc, total_examples = test_model(model, test_dataloader, device)

    # Return the evaluation metrics
    return MetricRecord({"accuracy": test_acc, "loss": test_loss, "num-examples": total_examples})
