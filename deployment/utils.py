import wandb
from model.lrcn import LRCN
from model.c3d import C3D
import utils
import cv2
import torch
import glob
import albumentations as A
import os
import random
from albumentations.pytorch import ToTensorV2
from torch.nn import functional as F



NUM_IMG_PER_VIDEO = 16

def get_default_agr(agrs):
    agrs['restore_file'] = 'best.pth'
    # DataModule specific args
    agrs['num_workers'] = 2

    if agrs['model_name'] == "lrcn":
        agrs['resize_to'] = 256
    elif agrs['model_name'] == "c3d":
        agrs['resize_to'] = 112
    
    return agrs


def get_model(agrs):

    NUM_CLASSES = 101
    MODEL_FILE = 'best.pth'
    
    run = wandb.init(project="dl_action_recognition", entity="dandl", anonymous='allow')
    # 2yy3abfb    
    args = get_default_agr(agrs)

    if args['model_name'] == 'lrcn':
        ARTIFACT_NAME = '3eyhjzfd_model'
    else:
        ARTIFACT_NAME = '38ckat92_model'
    
    artifact = run.use_artifact(f'dandl/dl_action_recognition/{ARTIFACT_NAME}:v0', type='model')

    # print(artifact.metadata)

    # return 
    print('Download started..')
    model = artifact.get_path(MODEL_FILE).download()
    print('Download completed!')
    
    args['device'] = utils.get_training_device()
    # dict_args = vars(args)

    # set everything
    utils.seed_everything(seed=73)

    # Get model 
    print(args)
    if args['model_name'] == "lrcn":
        net = LRCN(**args,
                    n_class=NUM_CLASSES)
    elif args['model_name'] == "c3d":
        net = C3D(**args,
                    n_class=NUM_CLASSES)
    
    net.to(args['device'])

    # Load weights
    load_checkpoint(f'./artifacts/{ARTIFACT_NAME}-v0/{MODEL_FILE}', net)
    
    run.finish()

    return net

def read_image(image_path, transform):
        
    img = cv2.imread(image_path)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
    if transform is not None:
        img = transform(image=img)['image']
    
    return img

def get_model_input(filename, args):
    
    model_name = args['model_name']

    lst_imgs = glob.glob(f'./deployment/staging/{model_name}/{filename}*')

    imgs = torch.stack([read_image(path, get_transforms(args)['test_transforms']) for path in lst_imgs], dim=0)

    return imgs.to(args['device'], non_blocking=True)
    
def feature_extraction(video_path, saved_path):
    try:
        width = 256
        height = 256
        sequence_length = 5 
        
        #Read the Video
        video_reader = cv2.VideoCapture(video_path)
        #get the frame count
        frame_count=int(video_reader.get(cv2.CAP_PROP_FRAME_COUNT))
        #Calculate the interval after which frames will be added to the list
        skip_interval = max(int(frame_count/sequence_length), 1)
        #iterate through video frames
        for counter in range(sequence_length):
            #Set the current frame postion of the video
            video_reader.set(cv2.CAP_PROP_POS_FRAMES, counter * skip_interval)
            #Read the current frame 
            ret, frame = video_reader.read()
            if not ret:
                break;
            #Resize the image
            frame = cv2.resize(frame, (height, width), interpolation = cv2.INTER_CUBIC)
            
            cv2.imwrite(saved_path + "_" + str(counter+1) + '.png', frame)
    except Exception as e:
        print("An error occured while extracting")
        print(e)
    print("Feature extraction completed")
    video_reader.release()

def feature_extraction1(video_path, saved_path, sequence_length=NUM_IMG_PER_VIDEO):
    video_reader = cv2.VideoCapture(video_path)
    #get the frame count
    frame_count=int(video_reader.get(cv2.CAP_PROP_FRAME_COUNT))
    
    #Calculate the starting point
    if frame_count < NUM_IMG_PER_VIDEO:
        raise NotImplementedError
        
    start_point = max(random.randint(0, frame_count-sequence_length-2), 0)
    
    #iterate through video frames
    for counter in range(sequence_length):
        #Set the current frame postion of the video
        video_reader.set(cv2.CAP_PROP_POS_FRAMES, start_point + counter)
        #Read the current frame 
        ret, frame = video_reader.read()
        if not ret:
            break;
            
        cv2.imwrite(saved_path + "_" + str(counter+1) + '.png', frame)
        
    video_reader.release()


def predict(input, filename, agrs):

    if agrs['model_name'] == 'lrcn':
        feature_extraction(input, f'./deployment/staging/lrcn/{filename}')
    else:
        print(agrs['model_name'])
        feature_extraction1(input, f'./deployment/staging/c3d/{filename}')
    net = get_model(agrs)

    img_inputs = get_model_input(filename, agrs)

    output = net(torch.unsqueeze(img_inputs, dim=0)).flatten()
    print(F.softmax(output, dim=0))

    return torch.topk(output, 10).indices, torch.topk(F.softmax(output, dim=0), 10).values
    
def get_transforms(args):

    train_transforms = A.Compose(
        [
            A.Resize(args['resize_to'], args['resize_to'], interpolation=cv2.INTER_CUBIC),
            A.Normalize(),
            ToTensorV2(),
        ]
        )
    val_transforms = A.Compose(
        [   
            A.Resize(args['resize_to'], args['resize_to'], interpolation=cv2.INTER_CUBIC),
            A.Normalize(),
            ToTensorV2(),
        ]
        )
    test_transforms = A.Compose(
        [   
            A.Resize(args['resize_to'], args['resize_to'], interpolation=cv2.INTER_CUBIC),
            A.Normalize(),
            ToTensorV2(),
        ]
        )
        
    return {'train_transforms': train_transforms, 
            'val_transforms': val_transforms,
            'test_transforms': test_transforms}


def load_checkpoint(checkpoint, model, optimizer=None):
    """Loads model parameters (state_dict) from file_path. If optimizer is provided, loads state_dict of
    optimizer assuming it is present in checkpoint.

    Args:
        checkpoint: (string) filename which needs to be loaded
        model: (torch.nn.Module) model for which the parameters are loaded
        optimizer: (torch.optim) optional: resume optimizer from checkpoint
    """

    if not os.path.exists(checkpoint):
        raise("File doesn't exist {}".format(checkpoint))
    
    checkpoint = torch.load(checkpoint, map_location=torch.device('cpu'))
    model.load_state_dict(checkpoint['state_dict']) #maybe epoch as well

    if optimizer:
        optimizer.load_state_dict(checkpoint['optim_dict'])

    return checkpoint





    