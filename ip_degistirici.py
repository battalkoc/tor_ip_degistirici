#!/usr/bin/env python3
"""Tor IP Değiştirici — Tor ağı üzerinden IP adresinizi belirli aralıklarla değiştirir."""

import argparse
import csv
import os
import re
import shutil
import signal
import sys
import time
from datetime import datetime

import iptablosu
from iptablosu import Hata, SaydamYonlendirme
from tor_kontrol import TorKontrol, TorKontrolHatasi, genel_ip, tor_hazir_bekle

SURUM = "2.0.0"
EN_KISA_ARALIK = 10  # Tor, NEWNYM isteklerini en fazla ~10 saniyede bir uygular
EN_FAZLA_DAKIKADA = 60 // EN_KISA_ARALIK


# ---------------------------------------------------------------- arayüz

RENKLI = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def boya(metin, kod):
    return "\033[{}m{}\033[0m".format(kod, metin) if RENKLI else metin


def yesil(m): return boya(m, "92")
def kirmizi(m): return boya(m, "91")
def sari(m): return boya(m, "93")
def mavi(m): return boya(m, "94")
def mor(m): return boya(m, "95")
def camgobegi(m): return boya(m, "96")
def soluk(m): return boya(m, "2")
def kalin(m): return boya(m, "1")


def bilgi(m): print(" {} {}".format(mavi("[*]"), m))
def basari(m): print(" {} {}".format(yesil("[+]"), m))
def uyari(m): print(" {} {}".format(sari("[!]"), m))
def hata(m): print(" {} {}".format(kirmizi("[x]"), m), file=sys.stderr)


BANNER = r"""
  ████████╗ ██████╗ ██████╗
  ╚══██╔══╝██╔═══██╗██╔══██╗
     ██║   ██║   ██║██████╔╝
     ██║   ██║   ██║██╔══██╗
     ██║   ╚██████╔╝██║  ██║
     ╚═╝    ╚═════╝ ╚═╝  ╚═╝"""


def banner_goster():
    print(mor(BANNER))
    print("   {}  {}\n".format(kalin("I P   D E Ğ İ Ş T İ R İ C İ"), soluk("v" + SURUM)))


def bayrak(ulke):
    if not ulke or len(ulke) != 2 or not ulke.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(harf) - ord("A")) for harf in ulke.upper())


def sure_bicimle(saniye):
    dakika, saniye = divmod(int(saniye), 60)
    saat, dakika = divmod(dakika, 60)
    return "{:02d}:{:02d}:{:02d}".format(saat, dakika, saniye)


def geri_sayim(saniye):
    for kalan in range(saniye, 0, -1):
        if RENKLI:
            print("\r   {} {} ".format(soluk("Sonraki değişim:"), camgobegi("{:>3} sn".format(kalan))),
                  end="", flush=True)
        time.sleep(1)
    if RENKLI:
        print("\r" + " " * 40 + "\r", end="", flush=True)


# ---------------------------------------------------------------- argümanlar

ORNEKLER = """
örnekler:
  sudo python3 ip_degistirici.py                 # etkileşimli
  sudo python3 ip_degistirici.py -d 2            # dakikada 2 kez
  sudo python3 ip_degistirici.py -a 30 -n 10     # 30 sn arayla 10 kez
  sudo python3 ip_degistirici.py -a 60 -u de,nl  # yalnızca Almanya/Hollanda çıkışları
  sudo python3 ip_degistirici.py -m proxy        # sistem trafiğine dokunmadan (SOCKS5)
  sudo python3 ip_degistirici.py --durum
  sudo python3 ip_degistirici.py --durdur        # kalmış kuralları temizle
"""


def arguman_ayristir():
    ayristirici = argparse.ArgumentParser(
        prog="ip_degistirici.py",
        description="Tor ağı üzerinden IP adresinizi belirli aralıklarla otomatik değiştirir.",
        epilog=ORNEKLER,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    zaman = ayristirici.add_mutually_exclusive_group()
    zaman.add_argument("-d", "--dakikada", type=int, metavar="SAYI",
                       help="dakikada kaç kez IP değişsin (1-{})".format(EN_FAZLA_DAKIKADA))
    zaman.add_argument("-a", "--aralik", type=int, metavar="SN",
                       help="iki değişim arası saniye (en az {})".format(EN_KISA_ARALIK))
    ayristirici.add_argument("-n", "--adet", type=int, default=0, metavar="SAYI",
                             help="toplam değişim sayısı, 0 = sınırsız (varsayılan)")
    ayristirici.add_argument("-u", "--ulke", metavar="KODLAR",
                             help="yalnızca bu ülkelerden çıkış yap, ör. de,nl,se")
    ayristirici.add_argument("-m", "--mod", choices=("saydam", "proxy"), default="saydam",
                             help="saydam: tüm sistem trafiği Tor'dan geçer (varsayılan); "
                                  "proxy: yalnızca SOCKS5 kullanan uygulamalar")
    ayristirici.add_argument("-g", "--gunluk", metavar="DOSYA", help="IP geçmişini CSV dosyasına kaydet")
    ayristirici.add_argument("--durum", action="store_true", help="Tor ve yönlendirme durumunu göster")
    ayristirici.add_argument("--durdur", action="store_true",
                             help="kalmış iptables kurallarını geri yükle, torrc'yi eski haline getir")

    gelismis = ayristirici.add_argument_group("gelişmiş")
    gelismis.add_argument("--kontrol-port", type=int, default=9051, metavar="PORT")
    gelismis.add_argument("--kontrol-sifre", metavar="ŞİFRE", default=os.environ.get("TOR_KONTROL_SIFRE"),
                          help="HashedControlPassword kullanıyorsanız (veya TOR_KONTROL_SIFRE)")
    gelismis.add_argument("--socks-port", type=int, default=9050, metavar="PORT")
    gelismis.add_argument("--trans-port", type=int, default=9040, metavar="PORT")
    gelismis.add_argument("--dns-port", type=int, default=9053, metavar="PORT")
    gelismis.add_argument("--renksiz", action="store_true", help="renkli çıktıyı kapat")
    ayristirici.add_argument("-v", "--version", action="version", version="%(prog)s " + SURUM)

    arg = ayristirici.parse_args()
    if arg.aralik is not None and arg.aralik < EN_KISA_ARALIK:
        ayristirici.error("--aralik en az {} saniye olmalı".format(EN_KISA_ARALIK))
    if arg.dakikada is not None and not 1 <= arg.dakikada <= EN_FAZLA_DAKIKADA:
        ayristirici.error("--dakikada 1 ile {} arasında olmalı".format(EN_FAZLA_DAKIKADA))
    if arg.adet < 0:
        ayristirici.error("--adet negatif olamaz")
    arg.ulkeler = []
    if arg.ulke:
        arg.ulkeler = [u.strip().lower() for u in arg.ulke.split(",") if u.strip()]
        gecersiz = [u for u in arg.ulkeler if not re.fullmatch(r"[a-z]{2}", u)]
        if gecersiz:
            ayristirici.error("geçersiz ülke kodu: {} (iki harfli olmalı, ör. de)".format(", ".join(gecersiz)))
    return arg


def araligi_belirle(arg):
    if arg.aralik is not None:
        return arg.aralik
    dakikada = arg.dakikada
    while dakikada is None:
        try:
            girdi = input(" {} Dakikada kaç kez değişsin? [{}] : ".format(
                camgobegi("[?]"), yesil("1-{}".format(EN_FAZLA_DAKIKADA))))
            deger = int(girdi)
        except ValueError:
            uyari("Lütfen bir sayı girin.")
            continue
        if 1 <= deger <= EN_FAZLA_DAKIKADA:
            dakikada = deger
        else:
            uyari("1 ile {} arasında bir değer girin.".format(EN_FAZLA_DAKIKADA))
    return 60 // dakikada


# ---------------------------------------------------------------- işlemler

def ip_sorgula(kontrol, socks_port, deneme=3):
    for sira in range(deneme):
        try:
            ip, tor_mu = genel_ip(socks_port)
            return ip, tor_mu, kontrol.ulke(ip) if ip else None
        except (OSError, ValueError, TorKontrolHatasi):
            if sira + 1 < deneme:
                time.sleep(2)
    return None, False, None


def satir_yaz(sira, ip, tor_mu, ulke, durum):
    zaman = soluk(datetime.now().strftime("%H:%M:%S"))
    if not ip:
        print("   {}  {}  {}".format(zaman, soluk("{:>4}".format(sira)), kirmizi("IP alınamadı")))
        return
    konum = "{} {}".format(bayrak(ulke), ulke) if ulke else soluk("??")
    tor = yesil("Tor ✓") if tor_mu else kirmizi("Tor ✗")
    print("   {}  {}  {}  {:<6}  {}  {}".format(zaman, soluk("{:>4}".format(sira)),
                                              kalin(yesil("{:<15}".format(ip))), konum, tor, durum))


def gunluge_yaz(dosya_yolu, ip, ulke, tor_mu):
    yeni = not os.path.isfile(dosya_yolu)
    with open(dosya_yolu, "a", newline="", encoding="utf-8") as dosya:
        yazici = csv.writer(dosya)
        if yeni:
            yazici.writerow(["zaman", "ip", "ulke", "tor"])
        yazici.writerow([datetime.now().isoformat(timespec="seconds"), ip or "", ulke or "", int(tor_mu)])


def dongu(kontrol, arg, aralik):
    socks = arg.socks_port if arg.mod == "proxy" else None
    baslangic = time.monotonic()
    gorulen = set()
    degisim = 0

    print()
    print("   {}".format(soluk("{:<8}  {:>4}  {:<15}  {:<5}  {:<5}  {}".format(
        "ZAMAN", "#", "IP", "KONUM", "TOR", "DURUM"))))
    ip, tor_mu, ulke = ip_sorgula(kontrol, socks)
    satir_yaz("—", ip, tor_mu, ulke, soluk("başlangıç"))
    if ip:
        gorulen.add(ip)
    if arg.gunluk:
        gunluge_yaz(arg.gunluk, ip, ulke, tor_mu)
    onceki = ip

    try:
        while arg.adet == 0 or degisim < arg.adet:
            geri_sayim(aralik)
            kontrol.yeni_kimlik()
            degisim += 1
            time.sleep(1)  # Yeni devrelerin kurulmasına zaman tanı
            ip, tor_mu, ulke = ip_sorgula(kontrol, socks)
            if not ip:
                durum = ""
            elif ip == onceki:
                durum = sari("aynı çıkış")
            elif ip in gorulen:
                durum = camgobegi("değişti (tekrar)")
            else:
                durum = yesil("değişti")
            satir_yaz(degisim, ip, tor_mu, ulke, durum)
            if ip:
                gorulen.add(ip)
                onceki = ip
            if arg.gunluk:
                gunluge_yaz(arg.gunluk, ip, ulke, tor_mu)
    finally:
        print()
        bilgi("Özet: {} kimlik yenileme, {} farklı IP, süre {}".format(
            kalin(degisim), kalin(len(gorulen)), kalin(sure_bicimle(time.monotonic() - baslangic))))


def calistir(arg, aralik):
    if arg.mod == "saydam" and not shutil.which("iptables"):
        raise Hata("iptables bulunamadı. Kurulum: sudo apt install iptables")

    ayarlar = iptablosu.torrc_ayarlari(arg.trans_port, arg.dns_port, arg.kontrol_port)
    if iptablosu.torrc_guncelle(ayarlar) or not iptablosu.tor_calisiyor_mu():
        bilgi("Tor (yeniden) başlatılıyor...")
        iptablosu.tor_servisi("restart")

    def ilerleme(yuzde):
        if RENKLI:
            dolu = yuzde // 5
            print("\r {} Tor ağına bağlanılıyor [{}{}] %{:<3}".format(
                mavi("[*]"), yesil("█" * dolu), soluk("░" * (20 - dolu)), yuzde), end="", flush=True)

    tor_hazir_bekle(arg.kontrol_port, arg.kontrol_sifre, ilerleme=ilerleme)
    if RENKLI:
        print()
    basari("Tor ağına bağlanıldı")

    yonlendirme = None
    with TorKontrol(arg.kontrol_port, arg.kontrol_sifre) as kontrol:
        try:
            if arg.ulkeler:
                kontrol.cikis_ulkesi_ayarla(arg.ulkeler)
                basari("Çıkış ülkeleri: {}".format(
                    "  ".join("{} {}".format(bayrak(u), u.upper()) for u in arg.ulkeler)))
            if arg.mod == "saydam":
                yonlendirme = SaydamYonlendirme(arg.trans_port, arg.dns_port)
                yonlendirme.etkinlestir()
                basari("Tüm trafik Tor üzerinden yönlendiriliyor — gizlilik {}".format(yesil("AÇIK")))
            else:
                bilgi("Proxy modu: uygulamalarınızı {} adresine ayarlayın".format(
                    kalin("SOCKS5 127.0.0.1:{}".format(arg.socks_port))))
            bilgi("Aralık: {} sn · Adet: {} · Çıkmak için {}".format(
                aralik, arg.adet or "sınırsız", kalin("Ctrl+C")))
            dongu(kontrol, arg, aralik)
        finally:
            if arg.ulkeler:
                try:
                    kontrol.cikis_ulkesi_ayarla(None)
                except (OSError, TorKontrolHatasi):
                    pass
            if yonlendirme and yonlendirme.devre_disi():
                basari("Önceki iptables kuralları geri yüklendi — gizlilik {}".format(kirmizi("KAPALI")))


def durum_goster(arg):
    tor_aktif = iptablosu.tor_calisiyor_mu()
    yonlendirme = SaydamYonlendirme(arg.trans_port, arg.dns_port).aktif_mi()
    print("   {:<22}{}".format("Tor servisi", yesil("ÇALIŞIYOR") if tor_aktif else kirmizi("DURMUŞ")))
    print("   {:<22}{}".format("Saydam yönlendirme", yesil("AKTİF") if yonlendirme else soluk("PASİF")))
    if not tor_aktif:
        return
    try:
        with TorKontrol(arg.kontrol_port, arg.kontrol_sifre) as kontrol:
            print("   {:<22}{}".format("Tor sürümü", kontrol.getinfo("version").split()[0]))
            print("   {:<22}%{}".format("Bağlantı", kontrol.bootstrap_yuzdesi()))
            ip, tor_mu, ulke = ip_sorgula(kontrol, arg.socks_port)
    except (OSError, TorKontrolHatasi) as h:
        uyari("Kontrol portuna bağlanılamadı: {}".format(h))
        return
    if ip:
        print("   {:<22}{}  {} {}  {}".format("Tor çıkış IP'si", kalin(yesil(ip)), bayrak(ulke), ulke or "??",
                                             yesil("Tor ✓") if tor_mu else kirmizi("Tor ✗")))
    else:
        uyari("Tor üzerinden IP alınamadı")


def durdur(arg):
    if SaydamYonlendirme(arg.trans_port, arg.dns_port).devre_disi():
        basari("iptables kuralları geri yüklendi")
    else:
        bilgi("Aktif yönlendirme bulunamadı")
    if iptablosu.torrc_temizle():
        iptablosu.tor_servisi("restart")
        basari("torrc eski haline getirildi, Tor yeniden başlatıldı")
    else:
        bilgi("torrc'de eklenmiş ayar bulunamadı")


def main():
    global RENKLI
    arg = arguman_ayristir()
    if arg.renksiz:
        RENKLI = False
    banner_goster()

    if not sys.platform.startswith("linux"):
        hata("Bu araç yalnızca Linux'ta çalışır.")
        return 1
    if os.geteuid() != 0:
        hata("Root yetkisi gerekli. Şöyle çalıştırın: {}".format(kalin("sudo python3 ip_degistirici.py")))
        return 1
    if not shutil.which("tor") and not os.path.exists("/usr/sbin/tor"):
        hata("Tor kurulu değil. Kurulum: {}".format(kalin("sudo ./kurulum.sh")))
        return 1

    # SIGTERM/SIGHUP geldiğinde de temizlik (finally blokları) çalışsın
    for sinyal in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sinyal, lambda *_: sys.exit(0))

    try:
        if arg.durdur:
            durdur(arg)
        elif arg.durum:
            durum_goster(arg)
        else:
            calistir(arg, araligi_belirle(arg))
    except KeyboardInterrupt:
        print()
        uyari("Kullanıcı tarafından durduruldu.")
    except (Hata, TorKontrolHatasi, OSError) as h:
        print()
        hata(str(h))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
