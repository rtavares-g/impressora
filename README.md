# Impressora — Raspberry Pi

Compartilha a **Samsung SCX-4200** (laser mono, ligada por USB no Raspberry Pi)
na rede local via **AirPrint**: iPhone, iPad e Mac encontram a impressora
sozinhos, sem instalar driver ou app.

## Como funciona

- **CUPS** recebe os trabalhos e usa o driver livre **splix**
  (`printer-driver-splix`, modelo `scx4200.ppd`) para falar a linguagem SPL
  da impressora.
- A fila é compartilhada, e o CUPS anuncia pelo **Avahi** (mDNS/Bonjour) o
  serviço `_ipp._tcp` com o subtipo `_universal`, que é o que os aparelhos
  Apple procuram para AirPrint.
- O acesso fica restrito à rede local: o CUPS aceita só sub-redes locais
  (`@LOCAL`), a administração remota fica desligada e o firewall libera a
  porta 631 só para `192.168.1.0/24`.

O scanner fica disponível pelo painel **Escaner** (abaixo), não como scanner de rede.

## Instalação

Com a impressora ligada e conectada na USB:

```bash
cd ~/projetos/impressora
./install.sh
```

O script instala os pacotes, cria a fila `Samsung_SCX4200` (papel A4, padrão
do sistema) e abre o firewall. Pode ser rodado de novo sem problema. Para
mudar o nome da fila ou a rede liberada:

```bash
FILA=Samsung REDE_LOCAL=192.168.0.0/24 ./install.sh
```

## Imprimindo

- **iPhone/iPad:** Compartilhar → Imprimir → escolha **Samsung SCX-4200**.
- **Mac:** aparece na lista de impressoras do diálogo de impressão.
- **No próprio Pi:** `lp -d Samsung_SCX4200 arquivo.pdf`

## Verificando

```bash
lpstat -p -d                                 # fila e impressora padrão
lpstat -o                                    # trabalhos na fila
avahi-browse -rt _ipp._tcp | grep -A8 SCX    # anúncio AirPrint (procure URF= e _universal)
journalctl -u cups -f                        # log do CUPS
```

## Painel do CUPS

O painel web vem desligado no Debian; o `install.sh` liga
(`cupsctl WebInterface=yes`) e tira a senha da administração para conexões
vindas do próprio Pi.

| De onde | Endereço |
|---|---|
| Fora de casa | <https://impressora.tavares.nz> (login do Cloudflare Access) |
| Rede de casa | <http://RaspberryPi-5.local:631> ou <http://192.168.1.15:631> (só consulta: fila e impressora) |
| No próprio Pi | <http://localhost:631> |

O acesso externo passa pelo Cloudflare Tunnel do Raspberry Pi
(`impressora.tavares.nz` → `http://localhost:631`) e é protegido pelo
Cloudflare Access. A rota do túnel precisa de **HTTP Host Header =
`localhost:631`**: em conexões vindas do próprio Pi (que é de onde o
`cloudflared` se conecta) o CUPS só aceita o nome `localhost` e responde
400 para qualquer outro, e `ServerAlias` não muda isso.

Pelo túnel o CUPS vê a conexão como local, então as páginas de
administração abrem direto depois do login do Access, sem pedir usuário e
senha do Pi: o `/admin` e as operações de administração (adicionar,
alterar, pausar impressora) não exigem senha, mas só são aceitos a partir de
`localhost`. Pela rede de casa a administração fica bloqueada (403). A impressão via AirPrint continua só na rede local.

## Tomada Tuya (liga sozinha)

A impressora fica numa tomada inteligente Tuya (app Smart Life). O serviço
`impressora-energia` (`energia.py`) olha a fila a cada 3 s:

- **Chegou trabalho e a impressora não está na USB** → liga a tomada. O
  backend USB do CUPS fica em "Waiting for printer to become available" e
  manda o trabalho quando ela aparece. O tempo até ficar pronta varia: no
  teste a USB voltou em uns 6 s, mas em dia frio o aquecimento demora mais.
  O serviço não tem prazo: se a USB não aparecer em 30 s ele só manda
  "ligar" de novo, e o CUPS continua esperando.
- **Fila vazia há 10 min** (contados do último trabalho ou de quando a
  impressora apareceu na USB) → desliga a tomada. Se você ligar na mão para
  tirar cópia, ela também desliga depois de 10 min.

A tomada é controlada pela rede local (protocolo Tuya local, sem nuvem), mas
para isso precisa do **Device ID** e da **Local Key**, que se conseguem uma
vez pela Tuya IoT Platform:

1. Crie uma conta em <https://platform.tuya.com> → **Cloud → Development →
   Create Cloud Project** (Development Method: Smart Home, Data Center:
   **Western America** se a conta do Smart Life for do Brasil). Anote o
   **Access ID** e o **Access Secret**.
2. No projeto, **Devices → Link App Account → Add App Account** e leia o QR
   com o Smart Life (Eu → ícone de leitura no canto). As tomadas aparecem na
   lista.
3. No Pi:

   ```bash
   cd ~/.config/impressora-energia && ~/projetos/impressora/.venv/bin/python -m tinytuya wizard
   ~/projetos/impressora/.venv/bin/python ~/projetos/impressora/energia.py configurar
   sudo systemctl restart impressora-energia
   ```

   O wizard pede o Access ID/Secret, a região (`us` para Western America) e
   o ID de algum dispositivo seu (Smart Life → dispositivo → lápis →
   Informações) e grava `devices.json` com as chaves. O `configurar` mostra a
   lista, você escolhe a tomada e ele salva `config.json` (fora do git).

**Rede:** o Raspberry Pi 5 fica em `192.168.1.15` (cabo, IP fixo no UniFi) e
a tomada na VLAN IoT (`10.10.0.0/24`, isolada) com IP fixo `10.10.0.65`. No
UniFi há uma regra *Pi -> tomada impressora (Tuya)* liberando só
`192.168.1.15` (Pi 5) e `192.168.1.10` (Home Assistant) → `10.10.0.65` TCP
6668 (com retorno), acima da "Isolated Networks". Se o Pi mudar de IP,
atualize a regra. Como a busca por broadcast não atravessa VLANs, o IP
fica fixo no `config.json`; se mudar, ajuste lá. A tomada (EKAZA 20A) usa o
protocolo Tuya **3.5**.

**Fila desabilitada:** quando a impressora sai da USB, o
`udev-configure-printer` desabilita a fila ("Unplugged or turned off") e nem
sempre reabilita quando ela volta. O serviço roda `cupsenable` quando ela
reaparece, só se o motivo for esse (falta de papel continua parada).

O tempo para desligar fica em `desligar_apos_min` no
`~/.config/impressora-energia/config.json`. A Local Key muda se a tomada for
removida e pareada de novo no app: aí rode o wizard e o `configurar` de novo.

**Trocando a tomada:** pareie a nova no Smart Life (ela aparece no projeto
Tuya sozinha), rode o wizard e o `configurar` de novo e dê a ela o IP fixo
`10.10.0.65` no UniFi (tirando a reserva da antiga), assim a regra de
firewall continua valendo. O `configurar` pede o IP quando não acha a tomada
por broadcast (o que sempre acontece entre VLANs) e testa as versões 3.5,
3.4 e 3.3 do protocolo até ela responder; se nenhuma responder, não salva.

```bash
~/projetos/impressora/.venv/bin/python ~/projetos/impressora/energia.py estado     # tomada e USB
~/projetos/impressora/.venv/bin/python ~/projetos/impressora/energia.py ligar      # ou desligar
journalctl -u impressora-energia -f
```

## Escaner

Painel web para digitalizar no vidro da impressora e baixar o PDF (ou
mandar por e-mail para outra pessoa):
<https://escaner.tavares.nz> (login do Cloudflare Access), servido pelo
serviço `escaner` (`escaner/servidor.py`, só Python + Pillow) em
`http://localhost:8082`.

- Escolha cor (cinza, colorido, P&B) e resolução (75, 100, 150, 200 ou 300 dpi; 600 trava o scanner) e toque
  em **Digitalizar página**. Para várias páginas, troque a folha e
  digitalize de novo; dá para remover uma página pelo ✕.
- Tocar numa miniatura abre a página em tela cheia (setas ou deslizar para
  passar); depois de digitalizar, a página nova abre sozinha.
- **Abrir PDF** junta as páginas num PDF só e abre numa nova guia (no
  iPhone não há download direto; salve de lá). **Novo documento** apaga as
  páginas. Cada aba tem seu documento (em `~/.cache/escaner/`, apagado
  depois de 6 h).
- **Enviar** manda o PDF por e-mail (até 5 endereços, separados por
  vírgula); o painel lembra os últimos endereços usados naquele navegador.
- Se a impressora estiver desligada, o painel toca
  `~/.config/impressora-energia/em-uso` e o `impressora-energia` liga a
  tomada; enquanto o painel usa o scanner ela não desliga.
- O nome do scanner no SANE (`xerox_mfp:libusb:…`) muda quando a impressora
  liga, por isso o painel procura de novo a cada digitalização.

Na rota do túnel: `escaner.tavares.nz` → `http://localhost:8082`.

**E-mail:** sai pela conta do iCloud (o domínio `tavares.nz` é domínio
personalizado do iCloud Mail). Crie uma senha de app em
<https://account.apple.com> → Entrar e Segurança → Senhas de app e grave
`~/.config/escaner/smtp.json` (fora do git, `chmod 600`):

```json
{
  "host": "smtp.mail.me.com",
  "porta": 587,
  "usuario": "<Apple ID>",
  "senha": "<senha de app>",
  "remetente": "Escaner <endereço@tavares.nz>"
}
```

O remetente precisa ser um endereço da conta (o próprio iCloud ou um do
domínio personalizado), escrito exatamente como foi criado, senão o iCloud
responde `550 From address is not one of your addresses`. O login é sempre o
Apple ID: entrar com o endereço do domínio dá `535 authentication failed`.
Não precisa reiniciar o serviço.

```bash
scanimage -L                     # o scanner aparece como xerox_mfp
journalctl -u escaner -f
```

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Não aparece no iPhone | Confira se o aparelho está na mesma rede (192.168.1.x) e se `avahi-browse` mostra a impressora |
| Trabalho fica parado | `lpstat -p`: se estiver "disabled", rode `cupsenable Samsung_SCX4200` |
| Trabalho falha com "Unable to send data to printer" e a impressora desconecta da USB | Geralmente é falta de papel: a impressora para de aceitar dados. Coloque papel e rode `cupsenable Samsung_SCX4200` |
| Trabalho parado em "Waiting for printer to become available" | A tomada não ligou: veja `journalctl -u impressora-energia` e `energia.py estado` |
| Impressora desligada ou reconectada | O CUPS retoma sozinho quando ela volta; se não, rode `./install.sh` de novo |
