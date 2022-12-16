#
# main file 
# 

import os
import argparse
from tqdm import tqdm

import torch
from torch import nn

from model.lrcn import LRCN
from model.c3d import C3D
from model.data_loader import ActionRecognitionDataWrapper 

from evaluate import evaluate

import utils
from utils import seed_everything, get_training_device, acc_metrics, get_lr, get_transforms


def get_arg_parser():

    parser = argparse.ArgumentParser()

    # PROGRAM level args
    parser.add_argument('--data_dir', type=str, required=True)
    parser.add_argument('--ckp_dir', type=str, default='./ckp/baseline')

    # DataModule specific args
    parser.add_argument('--dataset', type=str, required=True, choices=['hmdb51', 'ucf101'])
    parser.add_argument('--data_split', type=str, default='split1', choices=['split1', 'split2', 'split3'])
    parser.add_argument('--batch_size', type=int, default=64)
    parser.add_argument('--num_workers', type=int, default=2)

    # hyperparameters specific args
    parser.add_argument('--max_epochs', type=int, default=5)
    parser.add_argument('--lr', type=float, default=1e-4)
    parser.add_argument('--sl_gammar', type=float, default=0.999)

    # Module specific args
    ## which model to use
    parser.add_argument('--model_name', type=str, required=True, choices=["lrcn", "c3d"])

    ## Get the model name now 
    temp_args, _ = parser.parse_known_args()

    if temp_args.model_name == "lrcn":
        parser = LRCN.add_model_specific_args(parser)
    elif temp_args.model_name == "c3d":
        parser = C3D.add_model_specific_args(parser)

    # Wandb specific args
    parser.add_argument('--enable_wandb', action='store_true')
    parser.add_argument('--project', type=str, default="dl_action_recognition")
    parser.add_argument('--notes', type=str, default='')
    parser.add_argument('--wandb_ckpt', action='store_true')
    parser.add_argument('--save_loss_steps', type=int, default=10)


    return parser.parse_args()

def train(model, train_loader, criterion, optimizer, scheduler, wandb_logger, start_steps, args):
    
    model.train() 
    loss_avg = utils.RunningAverage()

    with tqdm(total=len(train_loader)) as t:
        for step , data in enumerate(train_loader):

            it = start_steps + step          # current global step

            image, label = data
            
            image = image.to(args.device, non_blocking=True)
            label = label.to(args.device, non_blocking=True)
            
            #forward
            output = model(image)
            loss = criterion(output, label)
            
            #backward
            loss.backward()
            optimizer.step() #update weight          
            optimizer.zero_grad() #reset gradient
        
            loss_avg.update(loss.item())

            if wandb_logger and it  % args.save_loss_steps == 0:

                wandb_logger._wandb.log({'train/loss': loss_avg()}, commit=False)
                wandb_logger._wandb.log({'train/lr': get_lr(optimizer)}, commit=False)
                wandb_logger._wandb.log({'trainer/global_step': it})

            t.set_postfix(loss='{:05.3f}'.format(loss_avg()))
            t.update()

    current_lr = get_lr(optimizer)

    #step scheduler   
    scheduler.step() 


    print("Learning Rate: {}..".format(current_lr),
          "Train Loss: {:.3f}..".format(loss_avg()))

def train_and_valid(epochs, model, train_loader, val_loader, criterion,  optimizer, scheduler, ckp_dir, wandb_logger, args):

    if wandb_logger:
        wandb_logger.set_steps()

    best_val_acc = 0.0 

    for epoch in range(epochs):
        
        print("Epoch {}/{}".format(epoch + 1, epochs))
        
        train(model, train_loader, criterion, optimizer, scheduler, wandb_logger, epoch * len(train_loader), args)

        val_loss, val_acc = evaluate(model, val_loader, criterion, acc_metrics, args)

        if wandb_logger:
            wandb_logger._wandb.log({
                                    'val/loss': val_loss,
                                    'val/acc': val_acc,
                                    'trainer/epoch': epoch + 1,
                                     },
                                     commit=False) # Let's the train loop commit

        # Logging
        is_best = val_acc >= best_val_acc
        if is_best:
            print("- Found new best accuracy performance")

        # Checkpoint saving
        utils.save_checkpoint({'epoch': epoch + 1,
                                'state_dict': model.state_dict(),
                                'optim_dict': optimizer.state_dict(),},
                                is_best=is_best,
                                checkpoint=ckp_dir)

        # Save the best
        if is_best:
            best_val_acc = val_acc

            b_json_path = os.path.join(ckp_dir, 'metrics_val_best_weights.json')
            utils.save_dict_to_json({'val_acc':val_acc}, b_json_path)

        # Save the last
        l_json_path = os.path.join(ckp_dir, 'metrics_val_last_weights.json')
        utils.save_dict_to_json({'val_acc':val_acc}, l_json_path) 

    #Finish the train and val loop and save artifact wandb
    if wandb_logger and args.wandb_ckpt and args.ckp_dir:
        wandb_logger._wandb.log({})  # Commit the last
        
        wandb_logger.log_info()
        wandb_logger.log_checkpoints()

if __name__ == '__main__':

    # Get parser argument
    args = get_arg_parser()
    # get training device
    args.device = get_training_device()
    dict_args = vars(args)

    # set everything
    seed_everything(seed=73)


    # Get data wrapper
    data_wrapper = ActionRecognitionDataWrapper(**dict_args, 
                                                transforms=get_transforms())
    
    # Get NUM_CLASSES
    if args.dataset == 'hmdb51':
        NUM_CLASSES = 51
    else:
        NUM_CLASSES = 101

    # Get model 
    if args.model_name == "lrcn":
        net = LRCN(**dict_args,
                    n_class=NUM_CLASSES)
    elif args.model_name == "c3d":
        net = C3D(**dict_args,
                    n_class=NUM_CLASSES)
    
    net.to(args.device)

    # Loss functions
    criterion = nn.CrossEntropyLoss()

    # Optimizer vs scheduler
    optimizer = torch.optim.Adam(params = net.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma = args.sl_gammar)

    # Wandb logger init
    if args.enable_wandb:
        wandb_logger = utils.WandbLogger(args)
    else:
        wandb_logger = None

    # Training
    train_and_valid(epochs=args.max_epochs, 
                    model=net, 
                    train_loader=data_wrapper.get_train_dataloader(), 
                    val_loader=data_wrapper.get_val_dataloader(), 
                    criterion=criterion, 
                    optimizer=optimizer, 
                    scheduler=scheduler, 
                    wandb_logger=wandb_logger,
                    ckp_dir=args.ckp_dir,
                    args=args
                    )

    # Finish wandb
    if wandb_logger:
        wandb_logger._wandb.finish()
