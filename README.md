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

A impressora não tem scanner em rede aqui: só a impressão é compartilhada.

## Instalação

Com a impressora ligada e conectada na USB:

```bash
cd ~/impressora
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

Painel web do CUPS (só no próprio Pi): <http://localhost:631>

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Não aparece no iPhone | Confira se o aparelho está na mesma rede (192.168.1.x) e se `avahi-browse` mostra a impressora |
| Trabalho fica parado | `lpstat -p`: se estiver "disabled", rode `cupsenable Samsung_SCX4200` |
| Impressora desligada ou reconectada | O CUPS retoma sozinho quando ela volta; se não, rode `./install.sh` de novo |
