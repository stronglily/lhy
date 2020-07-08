import torch
import torch.nn as nn
import torch.utils.data as data
import torch.nn.functional as F
import numpy as np
from tqdm import tqdm
import copy
import matplotlib.pyplot as plt

device = 'cpu'

"""
生成  𝑎∗sin(𝑥+𝑏)  的資料點，其中 𝑎,𝑏 的範圍分別預設為 [0.1,5],[0,2𝜋] ，
每一個  𝑎∗sin(𝑥+𝑏) 的函數有10個資料點當作訓練資料。
測試時則可用較密集的資料點直接由畫圖來看generalize的好壞。
"""
def meta_task_data(seed = 0, a_range=[0.1, 5], b_range = [0, 2*np.pi], task_num = 100,n_sample = 10, sample_range = [-5, 5], plot = False):
    np.random.seed = seed
    a_s = np.random.uniform(low = a_range[0], high = a_range[1], size = task_num)
    b_s = np.random.uniform(low = b_range[0], high = b_range[1], size = task_num)
    total_x = []
    total_y = []
    label = []
    for t in range(task_num):
        x = np.random.uniform(low = sample_range[0], high = sample_range[1], size = n_sample)
        total_x.append(x)
        total_y.append( a_s[t]*np.sin(x+b_s[t]) )
        label.append('{:.3}*sin(x+{:.3})'.format(a_s[t], b_s[t]))
    if plot:
        plot_x = [np.linspace(-5, 5, 1000)]
        plot_y = []
        for t in range(task_num):
            plot_y.append( a_s[t]*np.sin(plot_x+b_s[t]) )
        return total_x, total_y, plot_x, plot_y, label
    else:
        return total_x, total_y, label


"""
為了讓sub weight的gradient能夠傳到meta weight (因為sub weight的初始化是從meta weight來的，
所以想當然我們用sub weight算出來的loss對meta weight也應該是可以算gradient才對)，
這邊我們需要重新定義一些pytorch內的layer的運算。

實際上MetaLinear這個class做的事情跟torch.nn.Linear完全是一樣的，唯一的差別在於這邊的每一個tensor都沒有被變成torch.nn.Parameter。
這麼做的原因是因為等一下我們從meta weight那裏複製(init weight輸入meta weight後weight與bias使用.clone)的時候，
tensor的clone的操作是可以傳遞gradient的，以方便我們用gradient更新meta weight。
這個寫法的代價是我們就沒辦法使用torch.optim更新sub weight了，因為參數都只用tensor紀錄。
也因此我們接下來需要自己寫gradient update的函數(只用SGD的話是簡單的)。
"""
class MetaLinear(nn.Module):
    def __init__(self, init_layer=None):
        super(MetaLinear, self).__init__()
        if type(init_layer) != type(None):
            self.weight = init_layer.weight.clone()
            self.bias = init_layer.bias.clone()

    def zero_grad(self):
        self.weight.grad = torch.zeros_like(self.weight)
        self.bias.grad = torch.zeros_like(self.bias)

    def forward(self, x):
        return F.linear(x, self.weight, self.bias)


"""
這裡的forward和一般的model是一樣的，唯一的差別在於我們需要多寫一下__init__函數讓他比起一般的pytorch model多一個可以從meta weight複製的功能
(這邊因為我把model的架構寫死了所以可能看起來會有點多餘，讀者可以自己把net()改成可以自己調整架構的樣子，
然後思考一下如何生成一個跟meta weight一樣形狀的sub weight)

update函數就如同前一段提到的，我們需要自己先手動用SGD更新一次sub weight，接著再使用下一步的gradient(第二步)來更新meta weight。
zero_grad函數在此處沒有用到，因為實際上我們計算第二步的gradient時會需要第一步的grad，
這也是為什麼我們第一次backward的時候需要create_graph=True (建立計算圖以計算二階的gradient)
"""
class net(nn.Module):
    def __init__(self, init_weight=None):
        super(net, self).__init__()
        if type(init_weight) != type(None):
            for name, module in init_weight.named_modules():
                if name != '':
                    setattr(self, name, MetaLinear(module))
        else:
            self.hidden1 = nn.Linear(1, 40)
            self.hidden2 = nn.Linear(40, 40)
            self.out = nn.Linear(40, 1)

    def zero_grad(self):
        layers = self.__dict__['_modules']
        for layer in layers.keys():
            layers[layer].zero_grad()

    def update(self, parent, lr=1):
        layers = self.__dict__['_modules']
        parent_layers = parent.__dict__['_modules']
        for param in layers.keys():
            layers[param].weight = layers[param].weight - lr * parent_layers[param].weight.grad
            layers[param].bias = layers[param].bias - lr * parent_layers[param].bias.grad
        # gradient will flow back due to clone backward

    def forward(self, x):
        x = F.relu(self.hidden1(x))
        x = F.relu(self.hidden2(x))
        return self.out(x)


"""
前面的class中我們已經都將複製meta weight到sub weight，以及sub weight的更新，gradient的傳遞都搞定了，
meta weight自己本身的參數就可以按照一般pytorch model的模式，使用torch.optim來更新了。

gen_model函數做的事情其實就是產生N個sub weight，並且使用前面我們寫好的複製meta weight的功能。

注意到複製weight其實是整個code的關鍵，因為我們需要將sub weight計算的第二步gradient正確的傳回meta weight。
讀者從meta weight與sub weight更新參數作法的差別(手動更新/用torch.nn.Parameter與torch.optim)可以再思考一下兩者的差別。
"""
class Meta_learning_model():
    def __init__(self, init_weight = None):
        super(Meta_learning_model, self).__init__()
        self.model = net().to(device)
        if type(init_weight) != type(None):
            self.model.load_state_dict(init_weight)
        self.grad_buffer = 0

    def gen_models(self, num, check = True):
        models = [net(init_weight=self.model).to(device) for i in range(num)]
        return models

    def clear_buffer(self):
        print("Before grad", self.grad_buffer)
        self.grad_buffer = 0


if __name__ == '__main__':
    # 接下來就是生成訓練/測試資料，建立meta weightmeta weight的模型以及用來比較的model pretraining的模型
    bsz = 10
    train_x, train_y, train_label = meta_task_data(task_num=50000 * 10)
    train_x = torch.Tensor(train_x).unsqueeze(-1)  # add one dim
    train_y = torch.Tensor(train_y).unsqueeze(-1)
    train_dataset = data.TensorDataset(train_x, train_y)
    train_loader = data.DataLoader(dataset=train_dataset, batch_size=bsz, shuffle=False)

    test_x, test_y, plot_x, plot_y, test_label = meta_task_data(task_num=1, n_sample=10, plot=True)
    test_x = torch.Tensor(test_x).unsqueeze(-1)  # add one dim
    test_y = torch.Tensor(test_y).unsqueeze(-1)  # add one dim
    plot_x = torch.Tensor(plot_x).unsqueeze(-1)  # add one dim
    test_dataset = data.TensorDataset(test_x, test_y)
    test_loader = data.DataLoader(dataset=test_dataset, batch_size=bsz, shuffle=False)

    meta_model = Meta_learning_model()

    meta_optimizer = torch.optim.Adam(meta_model.model.parameters(), lr=1e-3)

    pretrain = net()
    pretrain.to(device)
    pretrain.train()
    pretrain_optim = torch.optim.Adam(pretrain.parameters(), lr=1e-3)

    """
    進行訓練，注意一開始我們要先生成一群sub weight(code裡面的sub models)，
    然後將一個batch的不同的sin函數的10筆資料點拿來訓練sub weight。
    注意這邊sub weight計算第一步gradient與第二步gradient時使用各五筆不重複的資料點(因此使用[:5]與[5:]來取)。
    但在訓練model pretraining的對照組時則沒有這個問題(所以pretraining的model是可以確實的走兩步gradient的)

    每一個sub weight計算完loss後相加(內層的for迴圈)後就可以使用optimizer來更新meta weight，
    再次提醒一下sub weight計算第一次loss的時候backward是需要create_graph=True的，
    這樣計算第二步gradient的時候才會真的計算到二階的項。讀者可以在這個地方思考一下如何將這段程式碼改成MAML的一階做法。
    """
    epoch = 1
    for e in range(epoch):
        meta_model.model.train()
        for x, y in tqdm(train_loader):
            x = x.to(device)
            y = y.to(device)
            sub_models = meta_model.gen_models(bsz)

            meta_l = 0
            for model_num in range(len(sub_models)):
                sample = torch.randint(0, 10, size=(10,), dtype=torch.long)

                # pretraining
                pretrain_optim.zero_grad()
                y_tilde = pretrain(x[model_num][sample[:5], :])
                little_l = F.mse_loss(y_tilde, y[model_num][sample[:5], :])
                little_l.backward()
                pretrain_optim.step()
                pretrain_optim.zero_grad()
                y_tilde = pretrain(x[model_num][sample[5:], :])
                little_l = F.mse_loss(y_tilde, y[model_num][sample[5:], :])
                little_l.backward()
                pretrain_optim.step()

                # meta learning

                y_tilde = sub_models[model_num](x[model_num][sample[:5], :])
                little_l = F.mse_loss(y_tilde, y[model_num][sample[:5], :])
                # 計算第一次gradient並保留計算圖以接著計算更高階的gradient
                little_l.backward(create_graph=True)
                sub_models[model_num].update(lr=1e-2, parent=meta_model.model)
                # 先清空optimizer中計算的gradient值(避免累加)
                meta_optimizer.zero_grad()

                # 計算第二次(二階)的gradient，二階的原因來自第一次update時有計算過一次gradient了
                y_tilde = sub_models[model_num](x[model_num][sample[5:], :])
                meta_l = meta_l + F.mse_loss(y_tilde, y[model_num][sample[5:], :])

            meta_l = meta_l / bsz
            meta_l.backward()
            meta_optimizer.step()
            meta_optimizer.zero_grad()

    # 測試我們訓練好的meta weight
    test_model = copy.deepcopy(meta_model.model)
    test_model.train()
    test_optim = torch.optim.SGD(test_model.parameters(), lr=1e-3)
    # 先畫出待測試的sin函數，以及用圓點點出測試時給meta weight訓練的十筆資料點
    fig = plt.figure(figsize=[9.6, 7.2])
    ax = plt.subplot(111)
    plot_x1 = plot_x.squeeze().numpy()
    ax.scatter(test_x.numpy().squeeze(), test_y.numpy().squeeze())
    ax.plot(plot_x1, plot_y[0].squeeze())
    # 分別利用十筆資料點更新meta weight以及pretrained model一個step
    test_model.train()
    pretrain.train()
    for epoch in range(1):
        for x, y in test_loader:
            y_tilde = test_model(x[0])
            little_l = F.mse_loss(y_tilde, y[0])
            test_optim.zero_grad()
            little_l.backward()
            test_optim.step()
            print("(meta)))Loss: ", little_l.item())

    for epoch in range(1):
        for x, y in test_loader:
            y_tilde = pretrain(x[0])
            little_l = F.mse_loss(y_tilde, y[0])
            pretrain_optim.zero_grad()
            little_l.backward()
            pretrain_optim.step()
            print("(pretrain)Loss: ", little_l.item())

    # 將更新後的模型所代表的函數繪製出來，與真實的sin函數比較
    test_model.eval()
    pretrain.eval()

    plot_y_tilde = test_model(plot_x[0]).squeeze().detach().numpy()
    plot_x2 = plot_x.squeeze().numpy()
    ax.plot(plot_x2, plot_y_tilde, label='tune(disjoint)')
    ax.legend()
    # fig.show()
    plt.show()

    plot_y_tilde = pretrain(plot_x[0]).squeeze().detach().numpy()
    plot_x2 = plot_x.squeeze().numpy()
    ax.plot(plot_x2, plot_y_tilde, label='pretrain')
    ax.legend()

    plt.savefig('sin.png')
    plt.show()

    # fig
