"""Small skeleton classifiers for V2-5. Input (N, 3, T, 17) as built by skeleton.py; output (N, classes) logits.
STGCN: three spatial-temporal graph blocks (Yan et al. 2018, simplified: one adjacency, a learnable edge weight per block), ~0.1 M parameters,
so it fits the edge budget (ROADMAP_V2 V2-5.2). TCN and MLP are the baselines the ladder starts with: if they do as well, the graph buys nothing."""
import numpy as np
import torch
from torch import nn

from .skeleton import EDGES


def adjacency() -> torch.Tensor:
    a = np.eye(17)
    for i, j in EDGES:
        a[i, j] = a[j, i] = 1
    a = a / a.sum(1, keepdims=True)                      # row-normalised, self loops included
    return torch.tensor(a, dtype=torch.float32)


class GraphBlock(nn.Module):
    def __init__(self, cin, cout, stride=1, k=9, drop=0.1):
        super().__init__()
        self.register_buffer("A", adjacency())
        self.edge = nn.Parameter(torch.ones(17, 17))     # learnable importance of each edge (kept on the existing edges by the mask)
        self.register_buffer("mask", (adjacency() > 0).float())
        self.spatial = nn.Conv2d(cin, cout, 1)
        self.temporal = nn.Sequential(nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
                                      nn.Conv2d(cout, cout, (k, 1), (stride, 1), (k // 2, 0)), nn.BatchNorm2d(cout), nn.Dropout(drop))
        self.res = nn.Identity() if cin == cout and stride == 1 else nn.Sequential(nn.Conv2d(cin, cout, 1, (stride, 1)), nn.BatchNorm2d(cout))
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):                                # x (N, C, T, V)
        a = self.A * (self.edge * self.mask)
        y = torch.einsum("nctv,vw->nctw", self.spatial(x), a)
        return self.relu(self.temporal(y) + self.res(x))


class STGCN(nn.Module):
    def __init__(self, classes=2, channels=(32, 64, 64), cin=5):
        super().__init__()
        self.bn = nn.BatchNorm1d(cin * 17)
        c = [cin, *channels]
        self.blocks = nn.Sequential(GraphBlock(c[0], c[1]), GraphBlock(c[1], c[2], 2), GraphBlock(c[2], c[3], 2))
        self.fc = nn.Linear(c[3], classes)

    def forward(self, x):
        x = features(x)                                   # (N, 5, T, V): x, y, valid and the velocity of x, y
        n, c, t, v = x.shape
        x = self.bn(x.permute(0, 1, 3, 2).reshape(n, c * v, t)).reshape(n, c, v, t).permute(0, 1, 3, 2)
        return self.fc(self.blocks(x).mean((2, 3)))


class TCN(nn.Module):
    """the same blocks with the graph replaced by identity: joints are just channels (no skeleton structure)"""

    def __init__(self, classes=2, hidden=96, cin=5, T=48):
        super().__init__()
        d = cin * 17
        self.net = nn.Sequential(nn.BatchNorm1d(d), nn.Conv1d(d, hidden, 9, padding=4), nn.BatchNorm1d(hidden), nn.ReLU(),
                                 nn.Conv1d(hidden, hidden, 9, 2, 4), nn.BatchNorm1d(hidden), nn.ReLU(),
                                 nn.Conv1d(hidden, hidden, 9, 2, 4), nn.BatchNorm1d(hidden), nn.ReLU(), nn.AdaptiveAvgPool1d(1), nn.Flatten(), nn.Linear(hidden, classes))

    def forward(self, x):
        x = features(x)
        n, c, t, v = x.shape
        return self.net(x.permute(0, 1, 3, 2).reshape(n, c * v, t))


class MLP(nn.Module):
    def __init__(self, classes=2, T=48, cin=5, hidden=128):
        super().__init__()
        self.net = nn.Sequential(nn.Flatten(), nn.Linear(cin * T * 17, hidden), nn.ReLU(), nn.Dropout(0.3), nn.Linear(hidden, hidden), nn.ReLU(), nn.Linear(hidden, classes))

    def forward(self, x):
        return self.net(features(x))


VELOCITY = True      # ablation switch: False = the network sees only x, y, valid (cin = 3)


def features(x: torch.Tensor) -> torch.Tensor:
    """(N, 3, T, V) -> (N, 5, T, V): add the frame-to-frame velocity of x and y (zero in the first frame). Part of the model, so it goes into the ONNX."""
    if not VELOCITY:
        return x
    v = x[:, :2, 1:] - x[:, :2, :-1]
    v = torch.cat([torch.zeros_like(x[:, :2, :1]), v], dim=2) * x[:, 2:3]
    return torch.cat([x, v], dim=1)


def build(name: str, T: int = 48):
    cin = 5 if VELOCITY else 3
    return {"stgcn": lambda: STGCN(cin=cin), "tcn": lambda: TCN(T=T, cin=cin), "mlp": lambda: MLP(T=T, cin=cin)}[name]()


def n_params(m: nn.Module) -> int:
    return sum(p.numel() for p in m.parameters())
