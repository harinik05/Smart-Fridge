###################################
# Name: Harini Karthik
# File_Name: Model_Training_Sequence
# Parent_File: Training.py
# Date: Feb. 23, 2024
# Reference: AzureML Sample Directory
###################################

# Import the required libraries for proj
import os 
import time 
import json 
import logging 
import pickle
import argparse 
import mlflow
from tqdm import tqdm 
from distutils.util import strobool 

# Import the torch libraries 
import torch 
import torch.nn as nn 
import torch.optim as optim
import torchvision 
from torch.optim import lr_scheduler 
from torch.utils.data.distributed import DistributedSampler
from torch.utils.data import DataLoader
from torch.profiler import record_function 

# Internal imports
from model import load_model, MODEL_ARCH_LIST
from image_io import build_image_datasets
from profiling import PyTorchProfilerHandler 

# Define a class for the Model_Training_Sequence
class Model_Training_Sequence:

    # Constructor 
    def __init__(self):
        # Intialize the logger object 
        self.logger = logging.getLogger(__name__)

        # Data
        self.training_data_sampler = None
        self.training_data_loader = None
        self.validation_data_loader = None

        # Model 
        self.model = None
        self.labels = []
        self.model_signature = None 

        # Distributed GPU Training Configuration 
        self.world_size = 1
        self.world_rank = 0
        self.local_world_size = 1
        self.local_rank = 0
        self.multinode_available = False 
        self.cpu_count = os.cpu_count()
        self.device = None
        self.self_is_main_node = True #flag to tell if ur in first node 

        # Training Configs 
        self.dataloading_config = None 
        self.training_config = None 

        # Profiler 
        self.profiler = None 
        self.profiler_output_tmp_dir = None 

    # Setup Configuration Function
    '''
    Number of workers: Refers to the number of worker processes that can be running for 
    data loading and training config job. If more than 0, then this will run paralleling 
    using multiple CPU cores to load data concurrently. 
    '''
    def setup_configuration(self, args):
        self.dataloading_config = args
        self.training_config = args 

        # Verify the parameter number of workers 
        if self.dataloading_config.num_workers is None:
            self.dataloading_config.num_workers = 0
        if self.dataloading_config.num_workers < 0:
            self.dataloading_config.num_workers = os.cpu_count()
        if self.dataloading_config.num_workers == 0:
            self.logger.warning("The number of workers is 0")
            self.dataloading_config.num_workers = None

        self.dataloading_config.pin_memory = bool(self.dataloading_config.pin_memory)
        self.dataloading_config.non_blocking = bool(self.dataloading_config.non_blocking)


        # Distributed: detect multinode config depending on Azure ML distribution type for DistributedDataParallel 
        self.distributed_backend = args.distributed_backend
        '''
        NCCL = NVIDIA Collective Communication Library for training across 
        multiple GPUs across a single node or across multiple nodes with GPUs
        from NVIDIA 

        MPI = Message Passing Interface for distributed computing
        '''
        if self.distributed_backend == "nccl":
            self.world_size = int(os.environ.get("WORLD_SIZE","1"))
            self.world_rank  =int(os.environ.get("RANK","0"))
            self.local_world_size = int(os.environ.get("LOCAL_WORLD_SIZE", "1"))
            self.local_rank = int(os.environ.get("LOCAL_RANK","0"))
            self.multinode_available = self.world_size>1
            self.self_is_main_node = self.world_rank == 0
        
        elif self.distributed_backend == "mpi":
            self.world_size = int(os.environ.get("OMPI_COMM_WORLD_SIZE","1"))
            self.world_rank = int(os.environ.get("OMPI_COMM_WORLD_RANK","0"))
            self.local_world_size = int(os.environ.get("OMPI_COMM_WORLD_LOCAL_SIZE","1"))
            self.local_rank = int(os.environ.get("OMPI_COMM_WORLD_LOCAL_RANK", 0))
            self.multinode_available = self.world_size>1
            self.self_is_main_node = self.world_rank==0

        else:
            raise NotImplementedError(f"the distributed backend {self.distributed_backend} is not implemented")
        
        # Check if CUDA is available for runnning PyTorch activities 
        if torch.cuda.is_available():
            self.logger.info(f"Setting up the torch.device for CUDA for local gpu: {self.local_rank}")
            self.device = torch.device(self.local_rank)
        
        else:
            self.logger.info(f"setting up torch.device for cpu")
            self.device = torch.device("cpu")
        
        # Check if the multinode optinon is available 
        if self.multinode_available:
            self.logger.info(f"Running in multinode with backend = {self.distributed_backend}, local_rank = {self.local_rank}, rank = {self.world_rank}, size = {self.world_size}")

            # Initializes the pytorch backend
            torch.distributed.init_process_group(
                self.distributed_backend,
                rank = self.world_rank,
                world_size = self.world_size,
            )
        
        else:
            self.logger.info(f"Not running in multinode")
        

        # Check if its the main node when rank = 0 (report from main process to avoid conflicts with distribution)
        if self.self_is_main_node:
            mlflow.log_params(
                {
                    # log some distribution params
                    "nodes": self.world_size // self.local_world_size,
                    "instance_per_node": self.local_world_size,
                    "cuda_available": torch.cuda.is_available(),
                    "cuda_device_count": torch.cuda.device_count(),
                    "distributed": self.multinode_available,
                    "distributed_backend": self.distributed_backend,
                    # data loading params
                    "batch_size": self.dataloading_config.batch_size,
                    "num_workers": self.dataloading_config.num_workers,
                    "prefetch_factor": self.dataloading_config.prefetch_factor,
                    "pin_memory": self.dataloading_config.pin_memory,
                    "non_blocking": self.dataloading_config.non_blocking,
                    # training params
                    "model_arch": self.training_config.model_arch,
                    "model_arch_pretrained": self.training_config.model_arch_pretrained,
                    "learning_rate": self.training_config.learning_rate,
                    "num_epochs": self.training_config.num_epochs,
                    # profiling params
                    "enable_profiling": self.training_config.enable_profiling,
                }
            )


    


