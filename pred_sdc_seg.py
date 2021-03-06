import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
import cv2
import torch
import gc
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.data as D
import torchvision.transforms as trans
import torch.optim as optimizer
import skimage.io as io
from sklearn.cluster import KMeans
!pip install torchsummary
import torchsummary as summary
plt.style.use('fivethirtyeight');
dev='cpu'
if torch.cuda.is_available():
    dev='cuda'
dev=torch.device(dev)

def read_img(path,img):
    img=io.imread(os.path.join(path,img))
    img,mask=img[:,:256].astype(np.float32),img[:,256:]
    return img,mask
train_images=[]
train_masks=[]
val_images=[]
val_masks=[]
path1='{train_path}'#train data path
path2='{val_path}'#test(val) data path
for image in os.listdir(path1):
    curr=read_img(path1,image)
    train_images.append(curr[0])
    train_masks.append(curr[1])
    del curr
    gc.collect()
for images in os.listdir(path2):
    curr=read_img(path1,image)
    val_images.append(curr[0])
    val_masks.append(curr[1])
    del curr
    gc.collect()

#clustering to accomplish fewer target pixels for segmentation
out_classes=10
color_A=np.random.choice(range(256),90000).reshape(-1,3)
cluster=KMeans(n_clusters=out_classes)
cluster.fit(color_A)
masks=[]
for i in range(len(train_masks)):
    masks.append(cluster.predict(train_masks[i].reshape(-1,3)).reshape(256,256))

class CreateDataset(D.Dataset):
    def __init__(self,x,y,trans=None):
        self.x=x
        self.y=y
        self.trans=trans
    def __len__(self):
        return len(self.x)
    def __getitem__(self,indx):
        xs=self.x[indx].reshape(3,256,256)
        ys=self.y[indx]
        xs=torch.tensor(xs)
        xs=self.trans(xs)
        ys=torch.tensor(ys).long()
        return xs,ys
train_dataset=CreateDataset(train_images,masks,trans=trans.Compose([trans.Normalize(mean=(0.485,0.456,0.406), std=(0.229,0.224,0.225))]))
Train=D.DataLoader(train_dataset,shuffle=True,batch_size=8,drop_last=True)
vmasks=[]
for i in range(len(val_masks)):
    vmasks.append(cluster.predict(val_masks[i].reshape(-1,3)).reshape(256,256))
val_dataset=CreateDataset(val_images,vmasks,trans=trans.Compose([trans.Normalize(mean=(0.485,0.456,0.406), std=(0.229,0.224,0.225))]))
Val=D.DataLoader(train_dataset,shuffle=True,batch_size=8,drop_last=True)

class DoubleConv(nn.Module):
    def __init__(self, in_channels, out_channels, mid_channels=None):
        super().__init__()
        if not mid_channels:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1, bias=False),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )
    def forward(self, x):
        return self.double_conv(x)
class Down(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels)
        )

    def forward(self, x):
        return self.maxpool_conv(x)
class Up(nn.Module):
    def __init__(self, in_channels, out_channels, bilinear=True):
        super().__init__()
        if bilinear:
            self.up = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=True)
            self.conv = DoubleConv(in_channels, out_channels, in_channels // 2)
        else:
            self.up = nn.ConvTranspose2d(in_channels, in_channels // 2, kernel_size=2, stride=2)
            self.conv = DoubleConv(in_channels, out_channels)

    def forward(self, x1, x2):
        x1 = self.up(x1)
        diffY = x2.size()[2] - x1.size()[2]
        diffX = x2.size()[3] - x1.size()[3]

        x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2,
                        diffY // 2, diffY - diffY // 2])
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)
class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)
    def forward(self, x):
        return self.conv(x)
class UNet(nn.Module):
    def __init__(self, n_channels, n_classes, bilinear=True):
        super(UNet, self).__init__()
        self.n_channels = n_channels
        self.n_classes = n_classes
        self.bilinear = bilinear

        self.inc = DoubleConv(n_channels, 64)
        self.down1 = Down(64, 128)
        self.down2 = Down(128, 256)
        self.down3 = Down(256, 512)
        self.down4 = Down(512, 1024 // 2)
        self.up1 = Up(1024, 512 // 2, bilinear)
        self.up2 = Up(512, 256 // 2, bilinear)
        self.up3 = Up(256, 128 // 2, bilinear)
        self.up4 = Up(128, 64, 2)
        self.outc = OutConv(64, n_classes)

    def forward(self, x):
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        logits = self.outc(x)
        return logits
net=UNet(3,out_classes)
net.to(dev)
summary.summary(net,(3,256,256))

loss_fn=nn.CrossEntropyLoss()
opt=optimizer.Adam(net.parameters(),lr=lr)
step_loss=[]
for e in range(220):
    L=0;
    S=0;
    for x,y in Train:
        opt.zero_grad()
        S+=16
        x,y=x.to(dev),y.to(dev)
        pred=net(x).squeeze(1)
        loss=loss_fn(pred,y)
        loss.backward()
        L+=loss.item()
        step_loss.append(loss.item());
        del loss,pred
        opt.step()
    if e%10==0:
        print(f'epoch {e+1} done:: loss {L/S}')

#plots and following statistic analysis been excluded
