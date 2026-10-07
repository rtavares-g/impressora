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

# Cada cupsctl faz o CUPS reiniciar; o seguinte falha com "Host está
# desligado" se rodar antes de ele voltar, então espera o agendador responder.
cupsctl_esperando() {
    for _ in $(seq 15); do
        sudo cupsctl "$@" 2>/dev/null && return
        sleep 2
    done
    sudo cupsctl "$@"
}

echo "==> Liberando o compartilhamento só na rede local (sem administração remota)"
cupsctl_esperando --share-printers --no-remote-admin --no-remote-any

echo "==> Ligando o painel web, com administração sem senha só a partir do próprio Pi"
# Pelo Cloudflare Tunnel (já protegido pelo Access) o CUPS vê a conexão como
# localhost; pela rede de casa /admin e as operações de administração seguem
# bloqueadas. Pode rodar de novo: só troca o que ainda estiver no padrão.
cupsctl_esperando WebInterface=yes
sudo python3 - /etc/cups/cupsd.conf <<'EOF'
import re, sys
p = sys.argv[1]
s = open(p).read()
s = re.sub(r"(<Location /admin[^>]*>\n)  AuthType Default\n  Require user @SYSTEM\n  Order allow,deny\n",
           r"\1  Order allow,deny\n  Allow localhost\n", s)
pol = re.search(r"<Policy default>.*?</Policy>", s, flags=re.S)
bloco = re.sub(r"(<Limit (?:CUPS-Add-Modify-Printer|Pause-Printer)[^>]*>\n)    AuthType Default\n    Require user @SYSTEM\n    Order deny,allow\n",
               r"\1    Order deny,allow\n    Deny all\n    Allow localhost\n", pol.group(0))
open(p, "w").write(s[:pol.start()] + bloco + s[pol.end():])
EOF
sudo systemctl restart cups

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

echo "==> Serviço que liga/desliga a tomada Tuya da impressora"
# tinytuya num venv; cryptography vem do apt para não compilar no Pi.
sudo apt-get install -y python3-venv python3-cryptography
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade tinytuya
mkdir -p "$HOME/.config/impressora-energia"
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$DIR|g" "$DIR/impressora-energia.service" \
    | sudo tee /etc/systemd/system/impressora-energia.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now impressora-energia
if [ ! -f "$HOME/.config/impressora-energia/config.json" ]; then
    echo "    Falta configurar a tomada: veja \"Tomada Tuya\" no README."
fi

echo "==> Painel de digitalização (escanear.tavares.nz -> http://localhost:8082)"
sudo apt-get install -y sane-utils python3-pil
sed -e "s|__USER__|$USER|g" -e "s|__DIR__|$DIR|g" "$DIR/escanear.service" \
    | sudo tee /etc/systemd/system/escanear.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable escanear
sudo systemctl restart escanear impressora-energia
if [ ! -f "$HOME/.config/escanear/smtp.json" ]; then
    echo "    Falta configurar o envio de e-mail: veja \"Escanear\" no README."
fi
