from ast import dump
import torch
from torchvision.models import vgg19
from torchvision import transforms
import pandas as pd
import numpy as np
from tqdm import tqdm, trange

import matplotlib.pyplot as plt
from model import ByLayerModel

from PIL import Image

import os
from functools import reduce
from datetime import datetime
import json

import torch.multiprocessing as mp

def load_image(path):
    image = Image.open(path)
    x = transforms.functional.to_tensor(image)
    x.unsqueeze_(0)

    return x

def gram_matrix(A):
    """
    A - Tensor
    """
    bs, a, b, c = A.size()
    # b=number of feature maps
    # (c,d)=dimensions of a f. map (N=c*d)

    features = A.view(bs, a ,b * c)  # resise F_XL into \hat F_XL

    G = torch.bmm(features, features.transpose(1, 2))  # compute the gram product

    # we 'normalize' the values of the gram matrix  \\\\ Yam's comment: why tf would you do that????? the def is called Gram matrix not 'normalized Gram matrix' -_-
    # by dividing by the number of element in each feature maps.
    return G  # .div(a * b * c )

def compute_style_loss(P, F, weights=None):
    """
    names of variables are uninformative, but follow the notation in the Gatys paper.
    P - list. a genereted vector of tensor of layers *batch size* num of filter* size of filter 
     P[l] is a matrix of size Nl x Ml, where Nl is the number of filters in the l-th layer of the VGG net and Ml is the number of elements in each filter.
        P contains the results of applying the net to the **original** image.

    F - list. similar to F, but applied on the **generated** image.
    weights - a manual rescaling weight given to each layer. If given, has to be a Tensor of size len(P)
    """
    
    num_layers = len(P)
    if weights is None:
        weights=torch.ones(num_layers, device=P[0].device)/num_layers
    loss = 0
    for l in range(num_layers):
        p, f = P[l], F[l]
        _, c, d, e = p.size()
        a, g = gram_matrix(p), gram_matrix(f) #check with batch size > 1
        loss += weights[l]* 1/((2*d*e*c)**2) * torch.linalg.norm(a-g) ** 2 
    return loss
        


def compute_layer_content_loss(C, G, weights=None):  
    """
    C-  list. a genereted vector of tensor of batch size* num of filter* size of filter 
    G-  list. a given vector of tensor of batch size* num of filter* size of filter 
    """

    num_layers = len(C)
    if weights is None:
        weights = torch.ones(num_layers) / num_layers

    loss = 0  #  torch.zeros(batch_size, device=C.device)
    for l in range(num_layers):
        p, f = C[l], G[l]
        loss += weights[l] * torch.linalg.norm(p-f) ** 2  # fixed the formula
    return loss / 2

    # return sum(map(lambda x: torch.linalg.norm(x[0]-x[1]) ** 2, zip(C, G))) / 2

         

def compute_loss(outputs, style_outputs, style_names, content_outputs, content_names, alpha, beta, style_weights=None, content_weights=None):
    """

    """
    x_style = [outputs[key] for key in outputs.keys() if key in style_names]  # cant stack as tensors might have different shapes for different layers
    x_con = [outputs[key]  for key in outputs.keys()  if key in content_names]
    y_style = [style_outputs[key] for key in style_outputs.keys()  if key in style_names]
    y_con = [content_outputs[key] for key in content_outputs.keys() if key in content_names]

    if style_weights is not None:
        style_weights = [style_weights[key] for key in style_names.keys()]
    if content_weights is not None:
        content_weights = [content_weights[key] for key in content_outputs.keys()]

    
    return alpha*compute_layer_content_loss(x_con,y_con, weights=content_weights) + beta*compute_style_loss(x_style, y_style, weights=style_weights)
    
    


def train(ephoch_num, input_size, style_image, content_image, alpha=1, beta=1e2, device="cuda", random_starts=1, verbose=True):

    inputs = torch.rand([random_starts] + list(input_size), requires_grad=True, device=device)

    layers = ["conv1_1", "relu1_1", "conv1_2","relu1_2", "maxpool1",
                                                         "conv2_1", "relu2_1", "conv2_2", "relu2_2", "maxpool2",
                                                         "conv3_1", "relu3_1", "conv3_2", "relu3_2", "conv3_3",
                                                            "relu3_3", "conv3_4", "relu3_4","maxpool3",
                                                         "conv4_1", "relu4_1", "conv4_2", "relu4_2", "conv4_3", 
                                                            "relu4_3", "conv4_4", "relu4_4", "maxpool4",
                                                        "conv5_1", "relu5_1", "conv5_2", "relu5_2", "conv5_3", 
                                                            "relu5_3", "conv5_4", "relu5_4", "maxpool5"]
    model = vgg19(pretrained=True).to(device)
    splitted_model = ByLayerModel(model.features, names=layers)

    for p in model.parameters():
        p.requires_grad_(False)

    style_image.requires_grad_(False)
    content_image.requires_grad_(False)

    loss_values = [] 

    # optimizer = torch.optim.SGD([inputs], lr=1e-5)
    # criterion = compute_content_loss
    
    style_names = ["conv1_1", "conv2_1", "conv3_1", "conv4_1"]  # 
    content_names = ["conv4_2"]

    assert(set(style_names).issubset(set(layers)))
    assert(set(content_names).issubset(set(layers)))
    

    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                    std=[0.229, 0.224, 0.225])  # following the documentation of VGG19
    
    transform = transforms.Compose([transforms.Resize(input_size[1:]), normalize])

    style_image = transform(style_image).to(device)
    content_image = transform(content_image).to(device)

    optimizer = torch.optim.Adam([inputs], lr=1e-3)
    criterion = compute_loss

    
    style_outputs = splitted_model(style_image)
    content_outputs = splitted_model(content_image)

    for epcoh_num in trange(ephoch_num, disable=not verbose):

        outputs = splitted_model(normalize(inputs))
        
        loss = criterion(outputs, style_outputs, style_names, content_outputs, content_names, alpha=alpha, beta=beta)
        
        loss.backward()

        loss_values.append(loss.item())

        optimizer.step()
        optimizer.zero_grad()

        with torch.no_grad():
            inputs.clamp_(0, 255)
        #     for i in inputs:
        #         i.clamp_(0, 255)
        # if before.equal(inputs):
        #     raise RuntimeError

        

    return inputs, loss_values 

def run_content_image(content_path):
        for style_name in os.listdir(STYLE_FOLDER):
 

            style_image = os.path.join(STYLE_FOLDER, style_name)
            content_image = os.path.join(CONTENT_FOLDER, content_name)

            style_image = load_image(style_image)
            content_image = load_image(content_image)



            inputs, loss_values = train(EPOCH_NUM, INPUT_SIZE, style_image, content_image, 
                                        alpha=ALPHA, beta=BETA, random_starts=RANDOM_STARTS, verbose=False)

            plt.rcParams["figure.figsize"] = (16, 9) 

            plt.semilogy(np.arange(len(loss_values)) + 1, loss_values, label=f"{str(style_name)[:-4]}")
            plt.legend()
            plt.savefig(os.path.join(output_folder, f"{str(content_name)[:-4]}_loss.png"))
            
            # img = inputs.detach().cpu().numpy()

            folder_name = os.path.join(output_folder, f"{str(content_name)[:-4]}", f"{str(style_name)[:-4]}")
            os.makedirs(folder_name)

            trasform = transforms.ToPILImage()

            for i, t in enumerate(inputs):
                
                img = trasform(t)
                img.save(os.path.join(folder_name, f"{i}.png"))

    

        plt.clf()





if __name__ == "__main__":
    EPOCH_NUM = 20000
    INPUT_SIZE = (3, 224, 224)
    SEED = 7442
    RANDOM_STARTS = 1
    ALPHA = 1
    BETA = 5e3

    configuration = {"epoch num": EPOCH_NUM, "input size": INPUT_SIZE, "SEED": SEED,
                     "RANDOM STARTS": RANDOM_STARTS, "ALPHA": ALPHA, "BETA": BETA}


    date = datetime.today()

    output_folder = os.path.join("outputs_all", date.strftime("%Y-%m-%d"), date.strftime("%H:%M"))

    os.makedirs(output_folder)

    with open(os.path.join(output_folder, "configuration.json"), "w", encoding="utf-8") as f:
        json.dump(configuration, f, ensure_ascii=False, indent=4)

    print(configuration)

    torch.manual_seed(SEED)


    CONTENT_FOLDER = "content"
    STYLE_FOLDER = "style_photos"

    procsesses = []

    for content_name in tqdm(os.listdir(CONTENT_FOLDER)):
        p = mp.Process(target=run_content_image, args=(run_content_image,))
        p.start()
        procsesses.append(p)


    for p in procsesses:
        p.join()    
