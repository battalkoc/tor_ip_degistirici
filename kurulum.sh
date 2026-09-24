#!/usr/bin/env bash
# Tor IP Değiştirici için gerekli paketleri kurar ve Tor servisini başlatır.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "[x] Root yetkisi gerekli: sudo ./kurulum.sh" >&2
  exit 1
fi

if command -v apt-get >/dev/null; then
  apt-get update
  apt-get install -y tor tor-geoipdb iptables python3
elif command -v dnf >/dev/null; then
  dnf install -y tor iptables python3
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm --needed tor iptables python
elif command -v zypper >/dev/null; then
  zypper install -y tor iptables python3
else
  echo "[x] Paket yöneticisi tanınmadı; tor, iptables ve python3'ü elle kurun." >&2
  exit 1
fi

if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  systemctl enable --now tor
else
  service tor start
fi

chmod +x "$(dirname "$0")/ip_degistirici.py"
echo "[+] Kurulum tamamlandı. Çalıştırmak için: sudo python3 ip_degistirici.py"
