"""Tor kontrol portu (ControlPort) istemcisi ve IP sorgulama yardımcıları."""

import json
import re
import socket
import ssl
import struct
import time
import urllib.request

IP_SUNUCUSU = "check.torproject.org"
IP_YOLU = "/api/ip"
KULLANICI_AJANI = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"


class TorKontrolHatasi(Exception):
    pass


class TorKontrol:
    """Tor'un kontrol protokolü için küçük, bağımlılıksız bir istemci."""

    def __init__(self, port=9051, sifre=None, host="127.0.0.1", zaman_asimi=15):
        self._soket = socket.create_connection((host, port), timeout=zaman_asimi)
        self._dosya = self._soket.makefile("rb")
        try:
            self._dogrula(sifre)
        except Exception:
            self.kapat()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.kapat()

    def kapat(self):
        for kaynak in (self._dosya, self._soket):
            try:
                kaynak.close()
            except OSError:
                pass

    def _satir_oku(self):
        ham = self._dosya.readline()
        if not ham:
            raise TorKontrolHatasi("Tor kontrol bağlantısı kapandı")
        return ham.decode("utf-8", "replace").rstrip("\r\n")

    def _gonder(self, komut):
        self._soket.sendall(komut.encode() + b"\r\n")
        satirlar = []
        while True:
            satir = self._satir_oku()
            kod, ayrac, icerik = satir[:3], satir[3:4], satir[4:]
            if ayrac == "+":  # Çok satırlı veri bloğu, tek "." satırıyla biter
                veri = [icerik]
                while True:
                    ek = self._satir_oku()
                    if ek == ".":
                        break
                    veri.append(ek)
                satirlar.append("\n".join(veri))
            else:
                satirlar.append(icerik)
            if ayrac == " ":
                if not kod.startswith("2"):
                    raise TorKontrolHatasi("{} başarısız: {} {}".format(komut.split()[0], kod, icerik))
                return satirlar

    def _dogrula(self, sifre):
        yanit = " ".join(self._gonder("PROTOCOLINFO 1"))
        eslesme = re.search(r"METHODS=(\S+)", yanit)
        yontemler = set(eslesme.group(1).split(",")) if eslesme else set()

        if "NULL" in yontemler:
            self._gonder("AUTHENTICATE")
        elif sifre is not None and "HASHEDPASSWORD" in yontemler:
            kacisli = sifre.replace("\\", "\\\\").replace('"', '\\"')
            self._gonder('AUTHENTICATE "{}"'.format(kacisli))
        elif "COOKIE" in yontemler:
            eslesme = re.search(r'COOKIEFILE="((?:[^"\\]|\\.)*)"', yanit)
            if not eslesme:
                raise TorKontrolHatasi("Tor çerez dosyasının yolunu bildirmedi")
            yol = eslesme.group(1).replace('\\"', '"').replace("\\\\", "\\")
            try:
                with open(yol, "rb") as dosya:
                    cerez = dosya.read()
            except PermissionError:
                raise TorKontrolHatasi("Çerez dosyası okunamadı ({}); root olarak çalıştırın".format(yol))
            self._gonder("AUTHENTICATE " + cerez.hex())
        else:
            raise TorKontrolHatasi(
                "Kimlik doğrulanamadı (desteklenen yöntemler: {}). "
                "Şifre kullanıyorsanız --kontrol-sifre verin.".format(", ".join(sorted(yontemler)) or "?"))

    def getinfo(self, anahtar):
        ilk = self._gonder("GETINFO " + anahtar)[0]
        return ilk.split("=", 1)[1].strip() if "=" in ilk else ilk

    def yeni_kimlik(self):
        """Yeni bağlantılar için yeni devre (dolayısıyla yeni çıkış IP'si) ister."""
        self._gonder("SIGNAL NEWNYM")

    def bootstrap_yuzdesi(self):
        eslesme = re.search(r"PROGRESS=(\d+)", self.getinfo("status/bootstrap-phase"))
        return int(eslesme.group(1)) if eslesme else 0

    def ulke(self, ip):
        """Tor'un kendi GeoIP veritabanından ülke kodunu döndürür."""
        try:
            kod = self.getinfo("ip-to-country/" + ip)
        except TorKontrolHatasi:
            return None
        return kod.upper() if kod and kod != "??" else None

    def cikis_ulkesi_ayarla(self, ulkeler):
        if ulkeler:
            dugumler = ",".join("{%s}" % ulke.lower() for ulke in ulkeler)
            self._gonder("SETCONF ExitNodes={} StrictNodes=1".format(dugumler))
        else:
            self._gonder("RESETCONF ExitNodes StrictNodes")


def tor_hazir_bekle(port, sifre=None, zaman_asimi=120, ilerleme=None):
    """Tor ağa tamamen bağlanana (bootstrap %100) kadar bekler."""
    bitis = time.monotonic() + zaman_asimi
    son_hata = None
    while time.monotonic() < bitis:
        try:
            with TorKontrol(port, sifre) as kontrol:
                yuzde = kontrol.bootstrap_yuzdesi()
            if ilerleme:
                ilerleme(yuzde)
            if yuzde >= 100:
                return
        except OSError as hata:  # Tor henüz dinlemiyor olabilir
            son_hata = hata
        time.sleep(1)
    raise TorKontrolHatasi("Tor {} sn içinde hazır olmadı ({})".format(zaman_asimi, son_hata or "bağlanıyor"))


# ---------------------------------------------------------------- IP sorgulama

def _tam_oku(soket, adet):
    veri = b""
    while len(veri) < adet:
        parca = soket.recv(adet - len(veri))
        if not parca:
            raise TorKontrolHatasi("SOCKS bağlantısı beklenmedik şekilde kapandı")
        veri += parca
    return veri


def _socks5_baglan(host, port, socks_port, zaman_asimi):
    soket = socket.create_connection(("127.0.0.1", socks_port), timeout=zaman_asimi)
    try:
        soket.sendall(b"\x05\x01\x00")
        if _tam_oku(soket, 2) != b"\x05\x00":
            raise TorKontrolHatasi("SOCKS5 el sıkışması başarısız")
        ad = host.encode("idna")
        soket.sendall(b"\x05\x01\x00\x03" + bytes([len(ad)]) + ad + struct.pack(">H", port))
        baslik = _tam_oku(soket, 4)
        if baslik[1] != 0:
            raise TorKontrolHatasi("SOCKS bağlantısı reddedildi (kod {})".format(baslik[1]))
        adres_uzunlugu = {1: 4, 4: 16}.get(baslik[3]) or _tam_oku(soket, 1)[0]
        _tam_oku(soket, adres_uzunlugu + 2)
        return soket
    except Exception:
        soket.close()
        raise


def _https_get_socks(host, yol, socks_port, zaman_asimi):
    ham = _socks5_baglan(host, 443, socks_port, zaman_asimi)
    with ssl.create_default_context().wrap_socket(ham, server_hostname=host) as soket:
        istek = "GET {} HTTP/1.0\r\nHost: {}\r\nUser-Agent: {}\r\nConnection: close\r\n\r\n"
        soket.sendall(istek.format(yol, host, KULLANICI_AJANI).encode())
        parcalar = []
        while True:
            parca = soket.recv(4096)
            if not parca:
                break
            parcalar.append(parca)
    baslik, _, govde = b"".join(parcalar).partition(b"\r\n\r\n")
    durum = baslik.split(b"\r\n", 1)[0]
    if b" 200" not in durum:
        raise TorKontrolHatasi("Beklenmeyen HTTP yanıtı: {}".format(durum.decode(errors="replace")))
    return govde


def genel_ip(socks_port=None, zaman_asimi=20):
    """(ip, tor_mu) döndürür. socks_port verilirse sorgu Tor SOCKS üzerinden yapılır."""
    if socks_port:
        govde = _https_get_socks(IP_SUNUCUSU, IP_YOLU, socks_port, zaman_asimi)
    else:
        # Ortamdaki proxy ayarlarını yok say; trafik iptables ile Tor'a gider
        acici = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        istek = urllib.request.Request("https://" + IP_SUNUCUSU + IP_YOLU,
                                       headers={"User-Agent": KULLANICI_AJANI})
        with acici.open(istek, timeout=zaman_asimi) as yanit:
            govde = yanit.read()
    veri = json.loads(govde.decode("utf-8"))
    return veri.get("IP"), bool(veri.get("IsTor"))
