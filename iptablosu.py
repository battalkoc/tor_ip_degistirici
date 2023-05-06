from subprocess import call, check_call, CalledProcessError, getoutput
from os import devnull
from os.path import isfile, basename
from sys import exit, stdout, stderr
from atexit import register
from json import load
from urllib.request import urlopen
from urllib.error import URLError
from time import sleep
import threading


class Tor_ip_tablosu:

  def __init__(self):
    self.virtual_net = "10.0.0.0/10"
    self.local_loopback = "127.0.0.1"
    self.local_dnsport = "53"
    self.tor_net = ["192.168.0.0/16", "172.16.0.0/12"]
    self.tor = ["127.0.0.0/9", "127.128.0.0/10", "127.0.0.0/8"]
    self.tor_id = getoutput("id -ur debian-tor")
    self.trans_port = "9040"
    self.tor_config_dosyasi = '/etc/tor/torrc'
    self.torrc = r'''
    ## Inserted by %s for tor iptables rules set
    ## Transparently route all traffic thru tor on port %s
    VirtualAddrNetwork %s
    AutomapHostsOnResolve 1
    TransPort %s
    DNSPort %s
    ''' % (basename(__file__), self.trans_port, self.virtual_net, self.trans_port, self.local_dnsport)


  def ip_degistirici(self):
    call(["iptables", "-F"])
    call(["iptables", "-t", "nat", "-F"])
    self.tor.extend(self.tor_net)

    @register
    def tor_restart():
      bknull = open(devnull, 'w')
      try:
        tor_restart = check_call(["service", "tor", "restart"], stdout=bknull, stderr=bknull)
        if isfile(self.tor_config_dosyasi):
            if not 'VirtualAddrNetwork' in open(self.tor_config_dosyasi).read():
                with open(self.tor_config_dosyasi, 'a+') as torrconf:
                    torrconf.write(self.torrc)
        if tor_restart == 0:
          print(" {0}".format("[\033[5;92m+\033[0m] Gizlilik Durumu [\033[5;92mACIK\033[0m]"))
          self.ip_bilgisi()
      except CalledProcessError as Err:
        print("\033[5;91m[!] Komut Başarısız: %s\033[0m" % ' '.join(Err.cmd))


    call(["iptables", "-I", "OUTPUT", "!", "-o", "lo", "!", "-d", self.local_loopback, "!", "-s", self.local_loopback, "-p", "tcp", "-m", "tcp", "--tcp-flags", "ACK,FIN", "ACK,FIN", "-j", "DROP"])
    call(["iptables", "-I", "OUTPUT", "!", "-o", "lo", "!", "-d", self.local_loopback, "!", "-s", self.local_loopback, "-p", "tcp", "-m", "tcp", "--tcp-flags", "ACK,RST", "ACK,RST", "-j", "DROP"])
    call(["iptables", "-t", "nat", "-A", "OUTPUT", "-m", "owner", "--uid-owner", "%s" % self.tor_id, "-j", "RETURN"])
    call(["iptables", "-t", "nat", "-A", "OUTPUT", "-p", "udp", "--dport", self.local_dnsport, "-j", "REDIRECT", "--to-ports", self.local_dnsport])

    for bk in self.tor:
      call(["iptables", "-t", "nat", "-A", "OUTPUT", "-d", "%s" % bk, "-j", "RETURN"])

    call(["iptables", "-t", "nat", "-A", "OUTPUT", "-p", "tcp", "--syn", "-j", "REDIRECT", "--to-ports", "%s" % self.trans_port])
    call(["iptables", "-A", "OUTPUT", "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"])

    for bk in self.tor:
      call(["iptables", "-A", "OUTPUT", "-d", "%s" % bk, "-j", "ACCEPT"])

    call(["iptables", "-A", "OUTPUT", "-m", "owner", "--uid-owner", "%s" % self.tor_id, "-j", "ACCEPT"])
    call(["iptables", "-A", "OUTPUT", "-j", "REJECT"])


  def ip_bilgisi(self):
    print(" {0}".format("[\033[5;95m*\033[0m] Public IP alınıyor, lütfen bekleyin..."))
    sayac = 0
    public_ip = None
    while sayac < 10 and not public_ip:
      sayac += 1
      try:
        public_ip = load(urlopen('https://check.torproject.org/api/ip'))['IP']
      except URLError:
        print(" [\033[5;93m?\033[0m] IP adresi bekleniyor...")
        sleep(2)
      except ValueError:
        break
    pass
    if not public_ip:
      my_public_ip = getoutput('wget -qO - ifconfig.me')
    if not public_ip:
      exit(" \033[5;91m[!]\033[0m Public ip addresi alınamıyor!")
    print(" {0}".format("[\033[5;96m$\033[0m] IP adresin \033[92m%s\033[0m" % public_ip))

tor_ip = Tor_ip_tablosu()
tor_ip.ip_degistirici()