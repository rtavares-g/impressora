#!/usr/bin/env bash
# Configura a Samsung SCX-4200 (USB) no CUPS e compartilha na rede local,
# onde iPhone/iPad/Mac a encontram como impressora AirPrint.
# Pode ser rodado de novo: recria a fila com a mesma configuração.
set -euo pipefail

FILA="${FILA:-Samsung_SCX4200}"
REDE_LOCAL="${REDE_LOCAL:-192.168.1.0/24}"
PPD="drv:///splix-samsung.drv/scx4200.ppd"

echo "==> Instalando CUPS e o driver splix (Samsung SPL)"
sudo apt-get update
sudo apt-get install -y cups cups-filters printer-driver-splix avahi-daemon
sudo usermod -aG lpadmin "$USER"

echo "==> Liberando o compartilhamento só na rede local (sem administração remota)"
sudo cupsctl --share-printers --no-remote-admin --no-remote-any

echo "==> Procurando a impressora na USB"
# O cupsctl faz o CUPS encerrar e o systemd subir de novo; até lá as
# consultas falham com "Connection reset by peer", então tenta algumas vezes.
URI=""
for _ in $(seq 15); do
    if DISPOSITIVOS="$(sudo lpinfo -v 2>/dev/null)"; then
        URI="$(awk '$2 ~ /^usb:\/\/Samsung\/SCX-4200/ {print $2; exit}' <<<"$DISPOSITIVOS")"
        break
    fi
    sleep 2
done
if [ -z "$URI" ]; then
    echo "Impressora não encontrada: confira o cabo USB e se ela está ligada (lsusb)." >&2
    exit 1
fi
echo "    $URI"

echo "==> Criando a fila $FILA"
sudo lpadmin -p "$FILA" -E -v "$URI" -m "$PPD" \
    -D "Samsung SCX-4200" -L "Raspberry Pi" \
    -o printer-is-shared=true -o PageSize=A4
sudo lpadmin -d "$FILA"

if command -v ufw >/dev/null && sudo ufw status | grep -q "Status: active"; then
    echo "==> Liberando IPP (631/tcp) e mDNS (5353/udp) no firewall para $REDE_LOCAL"
    sudo ufw allow from "$REDE_LOCAL" to any port 631 proto tcp comment "CUPS/AirPrint"
    sudo ufw allow from "$REDE_LOCAL" to any port 5353 proto udp comment "mDNS"
fi

echo
echo "Pronto. Fila: $FILA"
lpstat -p "$FILA"
echo "Teste local: lp -d $FILA /usr/share/cups/data/testprint"
