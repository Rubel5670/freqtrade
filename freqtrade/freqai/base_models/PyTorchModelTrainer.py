import logging
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import torch
import torch.nn as nn
from torch.optim import Optimizer
from torch.utils.data import DataLoader, TensorDataset


logger = logging.getLogger(__name__)


class PyTorchModelTrainer:
    def __init__(
            self,
            model: nn.Module,
            optimizer: Optimizer,
            criterion: nn.Module,
            device: str,
            batch_size: int,
            max_iters: int,
            max_n_eval_batches: int,
            init_model: Dict,
            model_meta_data: Dict[str, Any] = {},
    ):
        """
        :param model: The PyTorch model to be trained.
        :param optimizer: The optimizer to use for training.
        :param criterion: The loss function to use for training.
        :param device: The device to use for training (e.g. 'cpu', 'cuda').
        :param batch_size: The size of the batches to use during training.
        :param max_iters: The number of training iterations to run.
            iteration here refers to the number of times we call
            self.optimizer.step(). used to calculate n_epochs.
        :param max_n_eval_batches: The maximum number batches to use for evaluation.
        :param init_model: A dictionary containing the initial model/optimizer
         state_dict and model_meta_data saved by self.save() method.
        :param model_meta_data: Additional metadata about the model (optional).
        """
        self.model = model
        self.optimizer = optimizer
        self.criterion = criterion
        self.model_meta_data = model_meta_data
        self.device = device
        self.max_iters = max_iters
        self.batch_size = batch_size
        self.max_n_eval_batches = max_n_eval_batches

        if init_model:
            self.load_from_checkpoint(init_model)

    def fit(self, data_dictionary: Dict[str, pd.DataFrame]):
        """
         - Calculates the predicted output for the batch using the PyTorch model.
         - Calculates the loss between the predicted and actual output using a loss function.
         - Computes the gradients of the loss with respect to the model's parameters using
           backpropagation.
         - Updates the model's parameters using an optimizer.
        """
        data_loaders_dictionary = self.create_data_loaders_dictionary(data_dictionary)
        epochs = self.calc_n_epochs(
            n_obs=len(data_dictionary["train_features"]),
            batch_size=self.batch_size,
            n_iters=self.max_iters
        )
        for epoch in range(epochs):
            # evaluation
            losses = self.estimate_loss(data_loaders_dictionary, self.max_n_eval_batches)
            logger.info(
                f"epoch ({epoch}/{epochs}):"
                f" train loss {losses['train']:.4f} ; test loss {losses['test']:.4f}"
            )
            # training
            for batch_data in data_loaders_dictionary["train"]:
                xb, yb = batch_data
                xb = xb.to(self.device)
                yb = yb.to(self.device)
                yb_pred = self.model(xb)
                loss = self.criterion(yb_pred, yb)

                self.optimizer.zero_grad(set_to_none=True)
                loss.backward()
                self.optimizer.step()

    @torch.no_grad()
    def estimate_loss(
            self,
            data_loader_dictionary: Dict[str, DataLoader],
            max_n_eval_batches: Optional[int]
    ) -> Dict[str, float]:

        self.model.eval()
        loss_dictionary = {}
        n_batches = 0
        for split in ["train", "test"]:
            losses = []
            for i, batch in enumerate(data_loader_dictionary[split]):
                if max_n_eval_batches and i > max_n_eval_batches:
                    n_batches += 1
                    break

                xb, yb = batch
                xb = xb.to(self.device)
                yb = yb.to(self.device)
                yb_pred = self.model(xb)
                loss = self.criterion(yb_pred, yb)
                losses.append(loss.item())

            loss_dictionary[split] = sum(losses) / len(losses)

        self.model.train()
        return loss_dictionary

    def create_data_loaders_dictionary(
            self,
            data_dictionary: Dict[str, pd.DataFrame]
    ) -> Dict[str, DataLoader]:
        """
        Converts the input data to PyTorch tensors using a data loader.
        """
        data_loader_dictionary = {}
        for split in ["train", "test"]:
            labels_shape = data_dictionary[f"{split}_labels"].shape
            labels_view = labels_shape[0] if labels_shape[1] == 1 else labels_shape
            dataset = TensorDataset(
                torch.from_numpy(data_dictionary[f"{split}_features"].values).float(),
                torch.from_numpy(data_dictionary[f"{split}_labels"].astype(float).values)
                .long()
                .view(labels_view)
            )

            data_loader = DataLoader(
                dataset,
                batch_size=self.batch_size,
                shuffle=True,
                drop_last=True,
                num_workers=0,
            )
            data_loader_dictionary[split] = data_loader

        return data_loader_dictionary

    @staticmethod
    def calc_n_epochs(n_obs: int, batch_size: int, n_iters: int) -> int:
        """
        Calculates the number of epochs required to reach the maximum number
        of iterations specified in the model training parameters.
        """

        n_batches = n_obs // batch_size
        epochs = n_iters // n_batches
        return epochs

    def save(self, path: Path):
        """
        - Saving any nn.Module state_dict
        - Saving model_meta_data, this dict should contain any additional data that the
          user needs to store. e.g class_names for classification models.
        """

        torch.save({
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "model_meta_data": self.model_meta_data,
        }, path)

    def load_from_file(self, path: Path):
        checkpoint = torch.load(path)
        return self.load_from_checkpoint(checkpoint)

    def load_from_checkpoint(self, checkpoint: Dict):
        """
        when using continual_learning, DataDrawer will load the dictionary
        (containing state dicts and model_meta_data) by calling torch.load(path).
        you can access this dict from any class that inherits IFreqaiModel by calling
        get_init_model method.
        """

        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.model_meta_data = checkpoint["model_meta_data"]
        return self
