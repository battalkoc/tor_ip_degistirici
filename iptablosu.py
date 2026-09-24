"""Tor için torrc ayarları, servis yönetimi ve saydam yönlendirme (iptables) kuralları."""

import os
import pwd
import shutil
import subprocess

TORRC = "/etc/tor/torrc"
YEDEK_DIZINI = "/var/lib/tor_ip_degistirici"
BLOK_BASI = "## >>> tor_ip_degistirici (otomatik eklendi, elle düzenlemeyin) >>>"
BLOK_SONU = "## <<< tor_ip_degistirici <<<"

# Dağıtımlara göre Tor servisinin çalıştığı kullanıcı adları
TOR_KULLANICILARI = ("debian-tor", "tor", "_tor", "toranon")

# Tor'a yönlendirilmeyen, doğrudan erişilen ağlar (yerel ağ ve loopback)
YEREL_AGLAR = ("127.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12")


class Hata(Exception):
    pass


def _calistir(komut, girdi=None):
    try:
        sonuc = subprocess.run(komut, input=girdi, capture_output=True, text=True)
    except FileNotFoundError:
        raise Hata("Komut bulunamadı: {}".format(komut[0]))
    if sonuc.returncode != 0:
        raise Hata("Komut başarısız: {}\n    {}".format(" ".join(komut), sonuc.stderr.strip()))
    return sonuc.stdout


# ---------------------------------------------------------------- torrc

def torrc_ayarlari(trans_port, dns_port, kontrol_port):
    return [
        ("VirtualAddrNetworkIPv4", "10.192.0.0/10"),
        ("AutomapHostsOnResolve", "1"),
        ("TransPort", str(trans_port)),
        ("DNSPort", str(dns_port)),
        ("ControlPort", str(kontrol_port)),
        ("CookieAuthentication", "1"),
    ]


def _blogu_cikar(metin):
    satirlar, icerde = [], False
    for satir in metin.splitlines():
        if satir.strip() == BLOK_BASI:
            icerde = True
        elif satir.strip() == BLOK_SONU:
            icerde = False
        elif not icerde:
            satirlar.append(satir)
    return "\n".join(satirlar)


def _oku(yol):
    if not os.path.isfile(yol):
        return ""
    with open(yol, encoding="utf-8") as dosya:
        return dosya.read()


def _yaz(yol, metin):
    with open(yol, "w", encoding="utf-8") as dosya:
        dosya.write(metin)


def torrc_guncelle(ayarlar, yol=TORRC):
    """Ayar bloğunu torrc'ye yazar. Dosya değiştiyse True döner."""
    mevcut = _oku(yol)
    temel = _blogu_cikar(mevcut)
    # Kullanıcının kendi tanımladığı ayarları tekrar eklemeyelim
    tanimli = {
        satir.split()[0].lower()
        for satir in temel.splitlines()
        if satir.strip() and not satir.lstrip().startswith("#")
    }
    satirlar = ["{} {}".format(ad, deger) for ad, deger in ayarlar if ad.lower() not in tanimli]
    yeni = "{}\n\n{}\n{}\n{}\n".format(temel.rstrip("\n"), BLOK_BASI, "\n".join(satirlar), BLOK_SONU)
    if yeni == mevcut:
        return False
    _yaz(yol, yeni)
    return True


def torrc_temizle(yol=TORRC):
    """Eklenen bloğu torrc'den kaldırır. Dosya değiştiyse True döner."""
    mevcut = _oku(yol)
    if BLOK_BASI not in mevcut:
        return False
    _yaz(yol, _blogu_cikar(mevcut).rstrip("\n") + "\n")
    return True


# ---------------------------------------------------------------- Tor servisi

def tor_servisi(eylem):
    if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
        _calistir(["systemctl", eylem, "tor"])
    else:
        _calistir(["service", "tor", eylem])


def tor_calisiyor_mu():
    return subprocess.run(["pgrep", "-x", "tor"], capture_output=True).returncode == 0


def tor_kullanici_id():
    for ad in TOR_KULLANICILARI:
        try:
            return pwd.getpwnam(ad).pw_uid
        except KeyError:
            continue
    raise Hata("Tor kullanıcısı bulunamadı (denenenler: {})".format(", ".join(TOR_KULLANICILARI)))


# ---------------------------------------------------------------- iptables

class SaydamYonlendirme:
    """Tüm TCP ve DNS trafiğini Tor'un TransPort/DNSPort'una yönlendirir.

    Mevcut kurallar etkinleştirmeden önce yedeklenir ve devre dışı
    bırakıldığında aynen geri yüklenir.
    """

    def __init__(self, trans_port, dns_port, yedek_dizini=YEDEK_DIZINI):
        self.trans_port = str(trans_port)
        self.dns_port = str(dns_port)
        self.yedek4 = os.path.join(yedek_dizini, "iptables.yedek")
        self.yedek6 = os.path.join(yedek_dizini, "ip6tables.yedek")
        self.yedek_dizini = yedek_dizini

    def aktif_mi(self):
        return os.path.isfile(self.yedek4)

    def etkinlestir(self):
        tor_uid = str(tor_kullanici_id())
        os.makedirs(self.yedek_dizini, mode=0o700, exist_ok=True)
        # Önceki bir çökmeden kalan yedek varsa asıl kurallar odur, üzerine yazmayalım
        if not self.aktif_mi():
            self._yedekle()
        self._ipv4_kurallari(tor_uid)
        self._ipv6_kurallari()

    def devre_disi(self):
        if not self.aktif_mi():
            return False
        # iptables-save boş tabloları yazmaz; önce kendi zincirlerimizi temizleyelim
        _calistir(["iptables", "-F", "OUTPUT"])
        _calistir(["iptables", "-t", "nat", "-F", "OUTPUT"])
        _calistir(["iptables-restore"], girdi=_oku(self.yedek4))
        if os.path.isfile(self.yedek6):
            _calistir(["ip6tables", "-F", "OUTPUT"])
            _calistir(["ip6tables-restore"], girdi=_oku(self.yedek6))
            os.remove(self.yedek6)
        os.remove(self.yedek4)
        return True

    def _yedekle(self):
        _yaz(self.yedek4, _calistir(["iptables-save"]))
        if shutil.which("ip6tables-save"):
            try:
                _yaz(self.yedek6, _calistir(["ip6tables-save"]))
            except Hata:
                pass  # Çekirdekte IPv6 kapalı olabilir

    def _ipv4_kurallari(self, tor_uid):
        def ipt(*arg):
            _calistir(["iptables"] + list(arg))

        ipt("-F", "OUTPUT")
        ipt("-t", "nat", "-F", "OUTPUT")

        # nat: Tor'un kendi trafiği hariç DNS'i DNSPort'a, TCP'yi TransPort'a yönlendir
        ipt("-t", "nat", "-A", "OUTPUT", "-m", "owner", "--uid-owner", tor_uid, "-j", "RETURN")
        ipt("-t", "nat", "-A", "OUTPUT", "-p", "udp", "--dport", "53",
            "-j", "REDIRECT", "--to-ports", self.dns_port)
        for ag in YEREL_AGLAR:
            ipt("-t", "nat", "-A", "OUTPUT", "-d", ag, "-j", "RETURN")
        ipt("-t", "nat", "-A", "OUTPUT", "-p", "tcp", "--syn",
            "-j", "REDIRECT", "--to-ports", self.trans_port)

        # filter: saydam proxy sızıntılarını engelle, geri kalan her şeyi reddet
        for bayraklar in ("ACK,FIN", "ACK,RST"):
            ipt("-A", "OUTPUT", "!", "-o", "lo", "!", "-d", "127.0.0.1", "!", "-s", "127.0.0.1",
                "-p", "tcp", "-m", "tcp", "--tcp-flags", bayraklar, bayraklar, "-j", "DROP")
        ipt("-A", "OUTPUT", "-m", "conntrack", "--ctstate", "INVALID", "-j", "DROP")
        ipt("-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
        for ag in YEREL_AGLAR:
            ipt("-A", "OUTPUT", "-d", ag, "-j", "ACCEPT")
        ipt("-A", "OUTPUT", "-m", "owner", "--uid-owner", tor_uid, "-j", "ACCEPT")
        ipt("-A", "OUTPUT", "-j", "REJECT")

    def _ipv6_kurallari(self):
        # Tor saydam yönlendirmesi IPv6'yı kapsamaz; sızıntıyı önlemek için engelle
        if not os.path.isfile(self.yedek6):
            return
        for arg in (["-F", "OUTPUT"], ["-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"],
                    ["-A", "OUTPUT", "-j", "REJECT"]):
            _calistir(["ip6tables"] + arg)
