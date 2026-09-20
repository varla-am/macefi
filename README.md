# macEFI 
### This script builds Hackintosh EFI for you
### To use it you have to clone repository
## Requirements:
### Internet connection
### `git` package
### `Python` library
## How to install it:
### Run these commands:
```bash
git clone https://github.com/varla-am/macefi
cd macefi
```
### Now you need to know your operating system:
### To run it on windows, run:
```batch
install.bat
```
### To run it on macOS, run:
```zsh
chmod +x install.command
./install.command
source ~/.zshrc
```
### To run it on Linux distribution, run:
```bash
chmod +x install.sh
./install.sh
source ~/.bashrc
```
## How to use it:
### If you have Windows or Linux PC you have to run:
```bash
macefi detect
```
### If you have Darwin operating system (macOS) you have to run HardwareSniffer on PC you will be installing Hackintosh and import it to macEFI:
```bash
macefi plan --hw {PATH_TO_YOUR_REPORT.JSON}
```
### Now run this command and replace {PATH_TO_OUTPUT} to path where you want to be EFI and {PATH_TO_REPORT} to path where your hardware report is located:
```bash
macefi build --hw {PATH_TO_REPORT} -o {PATH_TO_OUTPUT}
```
### If you will edit EFI yourself, after changes you can validate EFI using this command:
```bash
macefi validate {PATH_TO_EFI}
```
