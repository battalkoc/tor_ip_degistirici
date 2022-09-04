from os import system
from time import sleep

system("apt-get install figlet")
system("clear")
system("figlet TOR IP DEGISTIRICI")
print("")
try:
  sure = int(input("Dakika'da kac defa degissin?[\033[92mmax:10\033[0m] : "))
  if (sure>10):
    print("\033[91mFazla giris yaptiniz!\033[0m")
    exit()
  if (sure<=0):
    print("\033[91mAz deger girdiniz!\033[0m")
    exit()
  sure = int(60/sure)
except ValueError:
    print("\033[91mBir deger girsene!\033[0m")
    exit()
while True:
	system("python iptablosu.py")
	sleep(sure)

