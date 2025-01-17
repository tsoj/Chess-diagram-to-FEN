# Import modules
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt

from src.fen_recognition import dataset
from src.fen_recognition.model import ChessRec
from src import common
import os
import datetime

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def get_accuracy_and_loss(loader, model, criterion):
    num_correct = 0
    num_samples = 0
    loss = 0.0
    model.eval()
    with torch.no_grad():
        for img, target in loader:
            img = img.to(device)
            target = target.to(device)
            output = model(img)

            loss += criterion(output, target).item() * target.size(0)

            output = output.cpu()
            target = target.cpu()

            assert output.size(0) == target.size(0)
            for i in range(0, output.size(0)):
                if (
                    common.tensor_to_chess_board(output[i]).fen()
                    == common.tensor_to_chess_board(target[i]).fen()
                ):
                    num_correct += 1

            num_samples += target.size(0)
    model.train()
    return num_correct / num_samples, loss / num_samples


LOSS_REPORT_FREQ = 200
TEST_ACC_FREQ = 4000


def train(
    data_root_dir="resources/fen_images/",
    outdir="models",
    total_steps=600_000,  # can also be (significantly) smaller while still producing acceptable results
    batch_size=8,
    max_lr=0.001,
    train_test_split=0.97,
    lr_schedule_pct_start=0.3,
    max_data=None,
    checkpoint=None
):
    start_time_string = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    print(start_time_string)

    if device.type == "cuda":
        print("Using GPU:", torch.cuda.get_device_name())
    else:
        print("Using CPU")

    chess_board_set = dataset.ChessBoardDataset(
        root_dir=data_root_dir,
        augment_ratio=0.8,
        affine_augment_ratio=0.8,
        max=max_data,
        device=device,
    )
    train_set, test_set = torch.utils.data.random_split(
        chess_board_set, [train_test_split, 1.0 - train_test_split]
    )

    train_loader = torch.utils.data.DataLoader(
        train_set, batch_size=batch_size, shuffle=True, drop_last=True
    )
    test_loader = torch.utils.data.DataLoader(
        test_set, batch_size=batch_size, shuffle=False, drop_last=True
    )

    model = ChessRec()
    if checkpoint is not None:
        model.load_state_dict(torch.load(checkpoint, map_location=torch.device("cpu")))
        print("Using checkpoint:", checkpoint)
    model.to(device)

    criterion = nn.MSELoss()
    optimizer = optim.AdamW(model.parameters())
    scheduler = optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=max_lr, total_steps=total_steps, pct_start=lr_schedule_pct_start
    )

    test_loss_list = []
    test_acc_list = []
    best_acc = -1.0
    best_model = None
    num_steps = 0

    while num_steps < total_steps:
        running_loss = 0.0

        for i, (img, target) in enumerate(train_loader):
            # Move data to device
            img = img.to(device)
            target = target.to(device)

            optimizer.zero_grad()

            output = model(img)

            loss = criterion(output, target)
            loss.backward()
            optimizer.step()
            scheduler.step()

            num_steps += 1

            running_loss += loss.item()

            if (i + 1) % LOSS_REPORT_FREQ == 0:
                print(
                    "[%d/%d, %5d] loss: %.4f, lr: %.5f"
                    % (
                        num_steps,
                        total_steps,
                        i + 1,
                        running_loss / LOSS_REPORT_FREQ,
                        optimizer.param_groups[0]["lr"],
                    )
                )
                running_loss = 0.0

            if (i + 1) % TEST_ACC_FREQ == 0 or num_steps >= total_steps:
                test_acc, test_loss = get_accuracy_and_loss(
                    test_loader, model, criterion
                )
                test_loss_list.append(test_loss)
                test_acc_list.append(test_acc)
                print(
                    "Num steps: %d, Test Loss: %.4f, Test Acc: %.3f"
                    % (num_steps, test_loss_list[-1], test_acc_list[-1])
                )

                if test_acc > best_acc:
                    best_acc = test_acc
                    best_model = model.state_dict()
                    print("Best model updated: Test Acc: %.3f" % best_acc)

            if num_steps >= total_steps:
                break

    os.makedirs(outdir, exist_ok=True)
    file_name = outdir + "/best_model_fen_%.3f_%s.pth" % (best_acc, start_time_string)
    print("Saving to", file_name)
    torch.save(best_model, file_name)

    # Plot the loss and accuracy curves
    plt.figure(figsize=(12, 4))
    plt.subplot(1, 2, 1)
    plt.plot(test_loss_list, label="Test Loss")
    plt.ylabel("Loss")
    plt.legend()
    plt.subplot(1, 2, 2)
    plt.plot(test_acc_list, label="Test Acc")
    plt.ylabel("Accuracy")
    plt.legend()
    plt.savefig(file_name + ".png", dpi=250)
