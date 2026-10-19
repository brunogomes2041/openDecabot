# ==============================================================================
# openDecabot - Controlador Completo em MicroPython para ESP32-C3 RISC-V
# ==============================================================================
# Recursos:
#  - Cria rede Wi-Fi própria: "MeuDecabot" (IP: 192.168.4.1)
#  - Servidor Web com botões de controle para celular (Touch / Sem recarregar tela)
#  - Suporte ao Display OLED I2C (SSD1306) com animação de olhos
#  - Controle dos dois servomotores 360 (PWM)
#  - Bip sonoro no buzzer ao iniciar
# ==============================================================================

import network
import socket
import time
import framebuf
from machine import Pin, PWM, I2C

# ------------------------------------------------------------------------------
# 1. PINAGEM DO ESP32-C3 SUPERMINI / PRO MINI
# ------------------------------------------------------------------------------
PIN_SERVO_DIR = 6   # GPIO 6
PIN_SERVO_ESQ = 7   # GPIO 7 (troque para 0 se sua fiação for no pino 0)
PIN_BUZZER    = 3   # GPIO 3
PIN_OLED_SDA  = 8   # GPIO 8
PIN_OLED_SCL  = 9   # GPIO 9
PIN_LED       = 8   # LED azul on-board

# Configurações da Rede Wi-Fi
SSID_NAME     = "BmoDecabot"
WIFI_PASS     = "Bruno007"  # Mínimo 8 caracteres para WPA2

# ------------------------------------------------------------------------------
# 2. DRIVER OLED SSD1306 EMBUTIDO (dispensa arquivos extras na placa)
# ------------------------------------------------------------------------------
class SSD1306(framebuf.FrameBuffer):
    def __init__(self, width, height, external_vcc=False):
        self.width = width
        self.height = height
        self.external_vcc = external_vcc
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def init_display(self):
        for cmd in (
            0xAE, 0x20, 0x00, 0x40, 0xA1, 0xA8, self.height - 1, 0xC8,
            0xD3, 0x00, 0x02 if self.width > 2 * self.height else 0x12,
            0xD5, 0x80, 0xD9, 0x22 if self.external_vcc else 0xF1,
            0xDB, 0x30, 0x81, 0xFF, 0xA4, 0xA6,
            0x8D, 0x10 if self.external_vcc else 0x14, 0xAF
        ):
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    def show(self):
        x0 = 0
        x1 = self.width - 1
        if self.width == 64:
            x0 += 32
            x1 += 32
        self.write_cmd(0x21)
        self.write_cmd(x0)
        self.write_cmd(x1)
        self.write_cmd(0x22)
        self.write_cmd(0)
        self.write_cmd(self.pages - 1)
        self.write_data(self.buffer)

class SSD1306_I2C(SSD1306):
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.i2c = i2c
        self.addr = addr
        self.temp = bytearray(2)
        self.write_list = [b"\x40", None]
        super().__init__(width, height, external_vcc)

    def write_cmd(self, cmd):
        self.temp[0] = 0x80
        self.temp[1] = cmd
        self.i2c.writeto(self.addr, self.temp)

    def write_data(self, buf):
        try:
            self.write_list[1] = buf
            self.i2c.writevto(self.addr, self.write_list)
        except Exception:
            self.i2c.writeto(self.addr, b'\x40' + buf)

# ------------------------------------------------------------------------------
# 3. CONFIGURAÇÃO DO BUZZER
# ------------------------------------------------------------------------------
def beep(freq=880, duration_ms=100):
    try:
        bz = PWM(Pin(PIN_BUZZER), freq=freq, duty_u16=32768)
        time.sleep_ms(duration_ms)
        bz.duty_u16(0)
        bz.deinit()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# 4. DISPLAY OLED (SSD1306) - AUTO-DETECTÁVEL COM FALLBACK
# ------------------------------------------------------------------------------
oled = None
try:
    i2c = I2C(0, sda=Pin(PIN_OLED_SDA), scl=Pin(PIN_OLED_SCL), freq=400000)
    oled = SSD1306_I2C(128, 64, i2c, addr=0x3C)
    print("Display OLED detectado com sucesso via Hardware I2C!")
except Exception as e1:
    try:
        from machine import SoftI2C
        i2c = SoftI2C(sda=Pin(PIN_OLED_SDA), scl=Pin(PIN_OLED_SCL), freq=400000)
        oled = SSD1306_I2C(128, 64, i2c, addr=0x3C)
        print("Display OLED detectado com sucesso via SoftI2C!")
    except Exception as e2:
        print("OLED nao detectado:", e2)

def animar_boot():
    """Mini animação de inicialização no painel OLED sincronizada com o buzzer"""
    if not oled:
        beep(587, 80)
        time.sleep_ms(50)
        beep(880, 120)
        return
    try:
        # 1. Efeito de expansão da moldura a partir do centro
        for step in range(1, 7):
            oled.fill(0)
            w = min(128, step * 22)
            h = min(64, step * 11)
            x = (128 - w) // 2
            y = (64 - h) // 2
            oled.rect(x, y, w, h, 1)
            oled.show()
            time.sleep_ms(25)

        beep(587, 70)

        # 2. Tela de Splash com faixa invertida e barra de carregamento progressiva
        etapas = [
            (20, "Iniciando..."),
            (45, "Motores OK..."),
            (75, "Ativando WiFi.."),
            (96, "Pronto!")
        ]
        for largura_barra, status_txt in etapas:
            oled.fill(0)
            oled.rect(0, 0, 128, 64, 1)
            # Faixa superior em alto contraste (fundo branco, texto preto)
            oled.fill_rect(2, 2, 124, 15, 1)
            oled.text("openDecabot", 20, 6, 0)

            # Barra de progresso central
            oled.rect(14, 25, 100, 12, 1)
            oled.fill_rect(16, 27, largura_barra, 8, 1)

            # Texto de status centralizado na parte inferior
            x_txt = max(2, (128 - (len(status_txt) * 8)) // 2)
            oled.text(status_txt, x_txt, 46, 1)
            oled.show()
            time.sleep_ms(220)

        beep(880, 120)
        time.sleep_ms(250)
    except Exception:
        pass

# Estado global da posição e expressão atual dos olhos para transições suaves
eye_cur_x = 0
eye_cur_y = 0
eye_cur_mood = "NORMAL"
eye_cur_sl = 0
eye_cur_sr = 0

def _desenha_olho_box(x, y, w, h, mood="NORMAL", is_left=True):
    """Desenha um olho com cantos arredondados e recortes de expressão (Estilo Cozmo/Vector)"""
    if h <= 5:
        oled.fill_rect(x, y, w, max(2, h), 1)
        return

    # Corpo principal com cantos suavizados (raio de 4px)
    r = 4 if h >= 14 else 2
    oled.fill_rect(x + r, y, w - 2 * r, h, 1)
    oled.fill_rect(x, y + r, w, h - 2 * r, 1)
    if r == 4:
        oled.fill_rect(x + 2, y + 1, w - 4, h - 2, 1)
        oled.fill_rect(x + 1, y + 2, w - 2, h - 4, 1)

    # Modificadores de expressão
    if mood == "FELIZ" and h >= 16:
        # Recorta a base em arco para formar olhinhos sorridentes (^ ^)
        cut_h = h // 2
        oled.fill_rect(x + 3, y + h - cut_h + 2, w - 6, cut_h, 0)
        oled.fill_rect(x + 6, y + h - cut_h, w - 12, cut_h, 0)
    elif mood == "FOCADO" and h >= 16:
        # Pálpebra superior inclinada (olhar veloz/determinado)
        for i in range(10):
            if is_left:
                # Inclinado descendo para a direita (centro)
                oled.hline(x + (i * 2), y + i // 2, w - (i * 2), 0)
            else:
                # Inclinado descendo para a esquerda (centro)
                oled.hline(x, y + i // 2, w - (i * 2), 0)

def desenha_olhos(offset_x=0, offset_y=0, blink_l=0, blink_r=None, mood="NORMAL", scale_l=0, scale_r=0):
    """
    Renderiza os dois olhos dinâmicos na tela 128x64.
    - offset_x: -18 a +18 (ou -1, 0, 1 compatível)
    - offset_y: -12 a +12
    - blink_l / blink_r: 0 (aberto) a 4 (fechado)
    - mood: "NORMAL", "FELIZ", "FOCADO", "SURPRESO"
    - scale_l / scale_r: variação de altura para perspectiva 3D / curiosidade
    """
    global eye_cur_x, eye_cur_y, eye_cur_mood, eye_cur_sl, eye_cur_sr
    if not oled:
        return
    if blink_r is None:
        blink_r = blink_l

    # Compatibilidade caso receba -1, 0, 1
    if offset_x in (-1, 1) and offset_y == 0 and scale_l == 0 and scale_r == 0:
        px = offset_x * 14
    else:
        px = offset_x

    eye_cur_x = px
    eye_cur_y = offset_y
    eye_cur_mood = mood
    eye_cur_sl = scale_l
    eye_cur_sr = scale_r

    try:
        oled.fill(0)
        base_w = 28
        base_h = 42 if mood == "SURPRESO" else 36

        # Olho esquerdo
        w_l = base_w + (scale_l // 2)
        max_h_l = max(6, base_h + scale_l)
        h_l = max(3, max_h_l - (blink_l * (max_h_l // 4)))
        x_l = 35 + px - (w_l // 2)
        y_l = (64 - h_l) // 2 + offset_y

        # Olho direito
        w_r = base_w + (scale_r // 2)
        max_h_r = max(6, base_h + scale_r)
        h_r = max(3, max_h_r - (blink_r * (max_h_r // 4)))
        x_r = 93 + px - (w_r // 2)
        y_r = (64 - h_r) // 2 + offset_y

        _desenha_olho_box(x_l, y_l, w_l, h_l, mood, is_left=True)
        _desenha_olho_box(x_r, y_r, w_r, h_r, mood, is_left=False)

        oled.show()
    except Exception:
        pass

def mover_olhos_suave(dest_x, dest_y=0, mood="NORMAL", scale_l=0, scale_r=0, passos=4):
    """Desliza os olhos suavemente da posição atual até a nova posição"""
    if not oled:
        return
    start_x = eye_cur_x
    start_y = eye_cur_y
    start_sl = eye_cur_sl
    start_sr = eye_cur_sr
    for i in range(1, passos + 1):
        cx = start_x + ((dest_x - start_x) * i) // passos
        cy = start_y + ((dest_y - start_y) * i) // passos
        csl = start_sl + ((scale_l - start_sl) * i) // passos
        csr = start_sr + ((scale_r - start_sr) * i) // passos
        desenha_olhos(cx, cy, 0, 0, mood, csl, csr)
        time.sleep_ms(18)

def piscar_olhos(shift_x=None, duplo=False):
    """Animação fluida de piscar os dois olhos mantendo a posição atual"""
    if not oled:
        return
    px = eye_cur_x if shift_x is None else (shift_x * 14 if shift_x in (-1, 0, 1) else shift_x)
    py = eye_cur_y
    mood = eye_cur_mood
    sl = eye_cur_sl
    sr = eye_cur_sr
    ciclos = 2 if duplo else 1
    for _ in range(ciclos):
        for b in [1, 3, 4, 2, 0]:
            desenha_olhos(px, py, b, b, mood, sl, sr)
            time.sleep_ms(22)
        if duplo:
            time.sleep_ms(60)

def piscar_um_olho():
    """Dá uma piscadinha charmosa com um olho só ;)"""
    if not oled:
        return
    for b in [1, 3, 4, 4, 2, 0]:
        desenha_olhos(eye_cur_x, eye_cur_y, 0, b, "NORMAL", 2, 0)
        time.sleep_ms(30)

def animar_acordar():
    """Mini animação quando o usuário se conecta + despertar expressivo dos olhos"""
    if not oled:
        return
    try:
        # 1. Alerta visual e sonoro de "CONECTADO!"
        beep(660, 60)
        for step in range(1, 5):
            oled.fill(0)
            w = step * 30
            h = step * 12
            x = (128 - w) // 2
            y = (64 - h) // 2
            oled.rect(x, y, w, h, 1)
            oled.show()
            time.sleep_ms(20)

        oled.fill(0)
        oled.rect(0, 0, 128, 64, 1)
        oled.fill_rect(4, 6, 120, 18, 1)
        oled.text("CONECTADO!", 24, 11, 0)
        oled.text("Controle Ativo", 8, 32, 1)
        oled.text("Vamos brincar!", 8, 46, 1)
        oled.show()
        beep(880, 70)
        time.sleep_ms(40)
        beep(1175, 110)
        time.sleep_ms(350)

        # 2. Olhos despertando (começam fechados e abrem piscando)
        for b in [4, 3, 2, 1, 0]:
            desenha_olhos(0, 0, b, b, "NORMAL")
            time.sleep_ms(35)
        piscar_olhos(duplo=True)

        # 3. Olha curioso para a esquerda e direita com efeito 3D
        mover_olhos_suave(-15, -2, "NORMAL", 5, -5, passos=4)
        time.sleep_ms(180)
        mover_olhos_suave(15, -2, "NORMAL", -5, 5, passos=5)
        time.sleep_ms(180)

        # 4. Volta ao centro e faz olhinhos felizes sorrindo (^ ^)
        mover_olhos_suave(0, -3, "FELIZ", 2, 2, passos=4)
        time.sleep_ms(350)
        mover_olhos_suave(0, 0, "NORMAL", 0, 0, passos=3)
    except Exception:
        pass

def tela_conexao(step=0):
    """Exibe informações de conexão Wi-Fi na tela até alguém se conectar"""
    if not oled:
        return
    try:
        oled.fill(0)
        # Cabeçalho centralizado
        oled.text("openDecabot", 20, 2, 1)
        oled.line(0, 12, 127, 12, 1)
        
        # Dados da rede
        oled.text(("WiFi: " + SSID_NAME)[:16], 0, 16, 1)
        oled.text("IP: 192.168.4.1", 0, 38, 1)
        
        oled.line(0, 49, 127, 49, 1)
        
        # Barra inferior com animação "Aguardando..."
        pontos = "." * (step % 4)
        msg = "Aguardando" + pontos
        x_pos = max(0, (128 - (len(msg) * 8)) // 2)
        oled.text(msg, x_pos, 53, 1)
        
        oled.show()
    except Exception:
        pass

# ------------------------------------------------------------------------------
# 4. CONFIGURAÇÃO DOS SERVOMOTORES 360 COM RAMPA SUAVE (ANTI-BROWNOUT)
# ------------------------------------------------------------------------------
s_dir = PWM(Pin(PIN_SERVO_DIR), freq=50)
s_esq = PWM(Pin(PIN_SERVO_ESQ), freq=50)

# Calibração do ponto neutro (repouso) de cada servo (~1.5ms pulse)
STOP_DIR = 4915
STOP_ESQ = 4915
SPEED    = 1600    # Velocidade alta / máxima (pulso de ~0.58ms a 2.41ms para rotação total)
SPEED_SLOW = SPEED // 2 # Velocidade para curvas diagonais (1500)

# Rastreamento da velocidade atual para rampa suave (Soft Start)
current_dir = STOP_DIR
current_esq = STOP_ESQ

def aplicar_velocidade_suave(target_dir, target_esq, passos=5, intervalo_ms=10):
    """Acelera rapidamente com rampa suave de 50ms para evitar quedas bruscas de tensão"""
    global current_dir, current_esq
    delta_dir = (target_dir - current_dir) // passos
    delta_esq = (target_esq - current_esq) // passos
    
    for i in range(passos):
        current_dir += delta_dir
        current_esq += delta_esq
        s_dir.duty_u16(current_dir)
        s_esq.duty_u16(current_esq)
        time.sleep_ms(intervalo_ms)
        
    current_dir = target_dir
    current_esq = target_esq
    s_dir.duty_u16(current_dir)
    s_esq.duty_u16(current_esq)

def parar():
    aplicar_velocidade_suave(STOP_DIR, STOP_ESQ, passos=4, intervalo_ms=10)
    desenha_olhos(0, 0, 0, 0, "NORMAL", 0, 0)

def frente():
    # Vai para frente com olhar focado/determinado
    aplicar_velocidade_suave(STOP_DIR - SPEED, STOP_ESQ + SPEED)
    desenha_olhos(0, -2, 0, 0, "FOCADO", 0, 0)

def tras():
    # Vai para trás com olhos arregalados/atentos
    aplicar_velocidade_suave(STOP_DIR + SPEED, STOP_ESQ - SPEED)
    desenha_olhos(0, 4, 0, 0, "SURPRESO", 0, 0)

def esquerda():
    # Gira para a esquerda (olhar 3D para a esquerda: olho esquerdo maior)
    aplicar_velocidade_suave(STOP_DIR - SPEED, STOP_ESQ - SPEED)
    desenha_olhos(-16, 0, 0, 0, "NORMAL", 6, -6)

def direita():
    # Gira para a direita (olhar 3D para a direita: olho direito maior)
    aplicar_velocidade_suave(STOP_DIR + SPEED, STOP_ESQ + SPEED)
    desenha_olhos(16, 0, 0, 0, "NORMAL", -6, 6)

def frente_esq():
    # Curva suave frente-esquerda com olhar focado na diagonal
    aplicar_velocidade_suave(STOP_DIR - SPEED, STOP_ESQ + SPEED_SLOW)
    desenha_olhos(-14, -3, 0, 0, "FOCADO", 5, -5)

def frente_dir():
    # Curva suave frente-direita com olhar focado na diagonal
    aplicar_velocidade_suave(STOP_DIR - SPEED_SLOW, STOP_ESQ + SPEED)
    desenha_olhos(14, -3, 0, 0, "FOCADO", -5, 5)

def tras_esq():
    # Curva suave trás-esquerda
    aplicar_velocidade_suave(STOP_DIR + SPEED, STOP_ESQ - SPEED_SLOW)
    desenha_olhos(-14, 4, 0, 0, "SURPRESO", 4, -4)

def tras_dir():
    # Curva suave trás-direita
    aplicar_velocidade_suave(STOP_DIR + SPEED_SLOW, STOP_ESQ - SPEED)
    desenha_olhos(14, 4, 0, 0, "SURPRESO", -4, 4)

# Inicializa motores em repouso
s_dir.duty_u16(STOP_DIR)
s_esq.duty_u16(STOP_ESQ)

# Executa a animação de inicialização no painel OLED
animar_boot()

# ------------------------------------------------------------------------------
# 5. CONFIGURAÇÃO DA REDE WI-FI ACCESS POINT
# ------------------------------------------------------------------------------
# 1. Desliga interfaces para resetar o rádio
try:
    sta = network.WLAN(network.STA_IF)
    sta.active(False)
except Exception:
    pass

ap = network.WLAN(network.AP_IF)
ap.active(False)
time.sleep_ms(200)

# 2. Ativa o Access Point
ap.active(True)
time.sleep_ms(300)

# 3. Configura nome e senha (MicroPython ativa WPA2 automaticamente ao passar password)
ap.config(essid=SSID_NAME, password=WIFI_PASS)

try:
    ap.config(channel=6)
except Exception:
    pass

try:
    ap.config(txpower=8.5)
except Exception:
    pass

# 4. Define o IP e inicia o servidor DHCP para entregar IP ao celular
ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '8.8.8.8'))
time.sleep(1) # Aguarda estabilização do rádio e do DHCP

print("------------------------------------------")
print("openDecabot Wi-Fi ativo!")
print("Nome da Rede :", SSID_NAME)
print("Senha        :", WIFI_PASS)
print("IP da Placa  :", ap.ifconfig()[0])
print("IP no browser: http://192.168.4.1")
print("------------------------------------------")

def check_stations():
    """Retorna o número de dispositivos conectados ao Wi-Fi (-1 se não suportado)"""
    try:
        return len(ap.status('stations'))
    except Exception:
        return -1

# Mostra tela de conexão com dados Wi-Fi logo na inicialização
tela_conexao(0)

# ------------------------------------------------------------------------------
# 6. PÁGINA HTML DE CONTROLE PARA O CELULAR
# ------------------------------------------------------------------------------
HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>openDecabot</title>
  <style>
    :root {
      --bg1: #060911;
      --bg2: #111827;
      --card: rgba(22, 29, 45, 0.85);
      --border: rgba(255, 255, 255, 0.09);
      --cyan: #00e5ff;
      --accent: #ff7b00;
      --accent-glow: rgba(255, 123, 0, 0.55);
      --txt: #f8fafc;
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      user-select: none;
      -webkit-user-select: none;
      touch-action: none;
      -webkit-tap-highlight-color: transparent;
    }
    body {
      background: radial-gradient(circle at 50% 15%, #1e293b 0%, var(--bg1) 75%);
      color: var(--txt);
      font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      min-height: 100vh;
      min-height: 100dvh;
      padding: 16px 14px 20px;
      overflow: hidden;
      position: relative;
    }

    /* --- TELA INICIAL DE CONEXÃO (OVERLAY CENTRAL) --- */
    #connect-overlay {
      position: fixed;
      inset: 0;
      z-index: 50;
      background: radial-gradient(circle at 50% 45%, #17253b 0%, #050811 80%);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px;
      transition: opacity 0.45s ease, transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
    }
    #connect-overlay.hide {
      opacity: 0;
      transform: scale(1.12);
      pointer-events: none;
    }
    .conn-title {
      font-size: 30px;
      font-weight: 800;
      letter-spacing: 1px;
      background: linear-gradient(135deg, #ffb703 0%, #fb5607 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 6px;
    }
    .conn-sub {
      font-size: 13px;
      color: #94a3b8;
      margin-bottom: 38px;
      letter-spacing: 0.5px;
    }
    .big-btn-wrap {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 32px;
    }
    .ring {
      position: absolute;
      width: 190px;
      height: 190px;
      border-radius: 50%;
      border: 2px solid rgba(0, 229, 255, 0.35);
      animation: ripple 2.2s infinite ease-out;
      pointer-events: none;
    }
    .ring.r2 {
      animation-delay: 1.1s;
    }
    @keyframes ripple {
      0% { transform: scale(0.85); opacity: 0.9; }
      100% { transform: scale(1.45); opacity: 0; }
    }
    #btn-conectar {
      width: 186px;
      height: 186px;
      border-radius: 50%;
      background: radial-gradient(circle at 35% 30%, #ff9e00 0%, #ea580c 65%, #9a3412 100%);
      border: 4px solid #fed7aa;
      box-shadow: 0 0 35px rgba(255, 123, 0, 0.55), inset 0 4px 12px rgba(255, 255, 255, 0.4);
      color: #fff;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      cursor: pointer;
      z-index: 2;
      transition: transform 0.15s ease, box-shadow 0.2s ease;
    }
    #btn-conectar:active {
      transform: scale(0.92);
      box-shadow: 0 0 50px rgba(0, 229, 255, 0.85);
    }
    #btn-conectar svg {
      width: 54px;
      height: 54px;
      fill: #fff;
      filter: drop-shadow(0 2px 6px rgba(0,0,0,0.35));
    }
    #btn-conectar span {
      font-size: 16px;
      font-weight: 800;
      letter-spacing: 1px;
      text-transform: uppercase;
      line-height: 1.2;
      text-align: center;
      text-shadow: 0 2px 4px rgba(0,0,0,0.4);
    }

    /* Mini animação de conexão no celular */
    #conn-anim {
      display: none;
      flex-direction: column;
      align-items: center;
      gap: 16px;
      width: 240px;
    }
    .spinner {
      width: 68px;
      height: 68px;
      border-radius: 50%;
      border: 4px solid rgba(0, 229, 255, 0.15);
      border-top-color: var(--cyan);
      border-right-color: var(--accent);
      animation: spin 0.75s infinite linear;
      box-shadow: 0 0 20px rgba(0, 229, 255, 0.3);
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .prog-bar {
      width: 100%;
      height: 10px;
      background: rgba(255,255,255,0.08);
      border-radius: 99px;
      overflow: hidden;
      border: 1px solid rgba(0, 229, 255, 0.3);
    }
    .prog-fill {
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--cyan));
      box-shadow: 0 0 12px var(--cyan);
      transition: width 0.3s ease;
    }
    #conn-msg {
      font-size: 14px;
      font-weight: 700;
      color: var(--cyan);
      letter-spacing: 0.6px;
    }

    /* --- PAINEL PRINCIPAL DO CONTROLE (Animação de Entrada) --- */
    #main-ui {
      width: 100%;
      height: 100%;
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      opacity: 0;
      transform: translateY(35px) scale(0.92);
      transition: opacity 0.5s ease, transform 0.55s cubic-bezier(0.34, 1.56, 0.64, 1);
      pointer-events: none;
    }
    #main-ui.show {
      opacity: 1;
      transform: translateY(0) scale(1);
      pointer-events: auto;
    }

    .header {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      width: 100%;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 1.2px;
      text-transform: uppercase;
      padding: 4px 12px;
      border-radius: 99px;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #34d399;
    }
    .dot {
      width: 7px;
      height: 7px;
      background: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 8px #10b981;
      animation: pulse 1.8s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }
    h1 {
      font-size: 26px;
      font-weight: 800;
      letter-spacing: 1px;
      background: linear-gradient(135deg, #ffb703 0%, #fb5607 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    /* Mini Visor OLED Virtual no Celular */
    .oled-screen {
      width: 150px;
      height: 68px;
      background: #020408;
      border: 2px solid #1e293b;
      border-radius: 16px;
      box-shadow: inset 0 0 18px rgba(0, 229, 255, 0.12), 0 6px 20px rgba(0,0,0,0.6);
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 24px;
      margin: 4px 0;
      position: relative;
      overflow: hidden;
    }
    .eye {
      width: 26px;
      height: 34px;
      background: var(--cyan);
      border-radius: 7px;
      box-shadow: 0 0 12px rgba(0, 229, 255, 0.8);
      transition: transform 0.15s cubic-bezier(0.34, 1.56, 0.64, 1), height 0.12s ease, border-radius 0.15s ease;
    }
    .oled-screen.happy .eye {
      height: 16px;
      border-radius: 14px 14px 4px 4px;
    }
    .oled-screen.blink .eye {
      height: 4px !important;
    }
    /* D-PAD Principal */
    .pad-wrap {
      background: var(--card);
      padding: 16px;
      border-radius: 34px;
      border: 1px solid var(--border);
      box-shadow: 0 18px 45px rgba(0,0,0,0.65), inset 0 1px 1px rgba(255,255,255,0.08);
    }
    .pad {
      display: grid;
      grid-template-columns: repeat(3, 86px);
      grid-template-rows: repeat(3, 86px);
      gap: 12px;
    }
    .btn {
      background: linear-gradient(180deg, #263147 0%, #171f2e 100%);
      color: var(--txt);
      border: 1px solid rgba(255,255,255,0.12);
      border-bottom: 4px solid #0d131f;
      border-radius: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: 0 6px 14px rgba(0,0,0,0.4);
      transition: all 0.08s ease;
    }
    .btn svg {
      width: 34px;
      height: 34px;
      fill: currentColor;
      filter: drop-shadow(0 2px 4px rgba(0,0,0,0.4));
    }
    .btn.diag {
      background: linear-gradient(180deg, #1b283d 0%, #111a29 100%);
      color: #38bdf8;
      border-radius: 20px;
    }
    .btn.diag svg {
      width: 26px;
      height: 26px;
    }
    .btn.stop {
      background: radial-gradient(circle, #7f1d1d 0%, #450a0a 100%);
      border: 2px solid #ef4444;
      border-bottom: 4px solid #2a0606;
      color: #fecaca;
      border-radius: 50%;
    }
    .btn:active, .btn.pressed {
      background: linear-gradient(180deg, #ff9e00 0%, #ff5400 100%) !important;
      color: #fff !important;
      border-color: #ffd166 !important;
      border-bottom-width: 1px !important;
      transform: translateY(3px) scale(0.95);
      box-shadow: 0 0 24px var(--accent-glow) !important;
    }
    /* Barra de Expressões */
    .actions {
      display: flex;
      gap: 10px;
      width: 100%;
      max-width: 316px;
      justify-content: center;
    }
    .act-btn {
      flex: 1;
      background: rgba(30, 41, 59, 0.8);
      border: 1px solid rgba(255,255,255,0.1);
      color: #cbd5e1;
      padding: 10px 8px;
      border-radius: 16px;
      font-size: 13px;
      font-weight: 600;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      box-shadow: 0 4px 10px rgba(0,0,0,0.3);
      transition: all 0.1s ease;
    }
    .act-btn:active {
      background: rgba(0, 229, 255, 0.2);
      border-color: var(--cyan);
      color: #fff;
      transform: scale(0.94);
    }
    /* Status Bar */
    .footer {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      width: 100%;
    }
    #status {
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.6px;
      padding: 7px 22px;
      border-radius: 99px;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid rgba(56, 189, 248, 0.3);
      color: var(--cyan);
      min-width: 210px;
      text-align: center;
      box-shadow: 0 0 12px rgba(0, 229, 255, 0.1);
    }
  </style>
</head>
<body>
  <!-- TELA CENTRAL DE CONEXÃO -->
  <div id="connect-overlay">
    <div class="conn-title">openDecabot</div>
    <p class="conn-sub">Toque abaixo para despertar o robô</p>

    <div class="big-btn-wrap" id="btn-wrap">
      <div class="ring"></div>
      <div class="ring r2"></div>
      <button id="btn-conectar" onclick="iniciarConexao()">
        <svg viewBox="0 0 24 24"><path d="M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42C17.99 7.86 19 9.81 19 12c0 3.87-3.13 7-7 7s-7-3.13-7-7c0-2.19 1.01-4.14 2.58-5.42L6.17 5.17C4.23 6.82 3 9.26 3 12c0 4.97 4.03 9 9 9s9-4.03 9-9c0-2.74-1.23-5.18-3.17-6.83z"/></svg>
        <span>Conectar<br>Decabot</span>
      </button>
    </div>

    <div id="conn-anim">
      <div class="spinner"></div>
      <div class="prog-bar"><div class="prog-fill" id="prog-fill"></div></div>
      <div id="conn-msg">Despertando Decabot...</div>
    </div>
  </div>

  <!-- INTERFACE DO CONTROLE (Aparece após clicar em Conectar) -->
  <div id="main-ui">
    <div class="header">
      <div class="badge"><span class="dot"></span>CONECTADO &bull; ESP32-C3</div>
      <h1>openDecabot</h1>
      <div class="oled-screen blink" id="visor">
        <div class="eye" id="eyeL"></div>
        <div class="eye" id="eyeR"></div>
      </div>
    </div>

    <div class="pad-wrap">
      <div class="pad">
        <button class="btn diag" data-cmd="frente_esq" data-x="-10" data-y="-6">
          <svg viewBox="0 0 24 24"><path d="M6 6h10v3H9.41l8.3 8.29-2.12 2.12L7.29 11.1V16H6V6z"/></svg>
        </button>
        <button class="btn" data-cmd="frente" data-x="0" data-y="-8">
          <svg viewBox="0 0 24 24"><path d="M12 4l-8 8h5v8h6v-8h5z"/></svg>
        </button>
        <button class="btn diag" data-cmd="frente_dir" data-x="10" data-y="-6">
          <svg viewBox="0 0 24 24"><path d="M18 6H8v3h6.59l-8.3 8.29 2.12 2.12 8.3-8.3V16H18V6z"/></svg>
        </button>

        <button class="btn" data-cmd="esquerda" data-x="-14" data-y="0">
          <svg viewBox="0 0 24 24"><path d="M4 12l8-8v5h8v6h-8v5z"/></svg>
        </button>
        <button class="btn stop" data-cmd="parar" data-x="0" data-y="0">
          <svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
        </button>
        <button class="btn" data-cmd="direita" data-x="14" data-y="0">
          <svg viewBox="0 0 24 24"><path d="M20 12l-8-8v5H4v6h8v5z"/></svg>
        </button>

        <button class="btn diag" data-cmd="tras_esq" data-x="-10" data-y="6">
          <svg viewBox="0 0 24 24"><path d="M6 18h10v-3H9.41l8.3-8.29-2.12-2.12L7.29 12.9V8H6v10z"/></svg>
        </button>
        <button class="btn" data-cmd="tras" data-x="0" data-y="8">
          <svg viewBox="0 0 24 24"><path d="M12 20l8-8h-5V4H9v8H4z"/></svg>
        </button>
        <button class="btn diag" data-cmd="tras_dir" data-x="10" data-y="6">
          <svg viewBox="0 0 24 24"><path d="M18 18H8v-3h6.59l-8.3-8.29 2.12-2.12 8.3 8.3V8H18v10z"/></svg>
        </button>
      </div>
    </div>

    <div class="actions">
      <button class="act-btn" onclick="triggerAct('feliz', 'Modo Feliz ^-^')">&#128522; Feliz</button>
      <button class="act-btn" onclick="triggerAct('piscar', 'Piscadinha ;)')">&#128521; Piscar</button>
      <button class="act-btn" onclick="triggerAct('bip', 'Bip Sonoro!')">&#128266; Buzina</button>
    </div>

    <div class="footer">
      <div id="status">PRONTO PARA COMANDO</div>
    </div>
  </div>

  <script>
    let activeCmd = null;
    let isConnected = false;
    const statusEl = document.getElementById('status');
    const visor = document.getElementById('visor');
    const eyeL = document.getElementById('eyeL');
    const eyeR = document.getElementById('eyeR');

    function vib(ms) {
      if (navigator.vibrate) try { navigator.vibrate(ms); } catch(e) {}
    }

    function iniciarConexao() {
      if (isConnected) return;
      isConnected = true;
      vib([30, 50, 40]);

      // Aciona o despertar dos olhinhos no Decabot físico imediatamente
      fetch('/conectar').catch(() => {});

      // Mostra mini animação de conexão no celular
      document.getElementById('btn-wrap').style.display = 'none';
      const animBox = document.getElementById('conn-anim');
      const prog = document.getElementById('prog-fill');
      const msg = document.getElementById('conn-msg');
      animBox.style.display = 'flex';

      setTimeout(() => { prog.style.width = '45%'; msg.innerText = 'Acordando olhinhos...'; }, 80);
      setTimeout(() => { prog.style.width = '85%'; msg.innerText = 'Carregando controles...'; }, 450);
      setTimeout(() => { prog.style.width = '100%'; msg.innerText = 'Conectado!'; vib(25); }, 820);

      // Transição suave para exibir os controles e abrir os olhinhos virtuais
      setTimeout(() => {
        document.getElementById('connect-overlay').classList.add('hide');
        document.getElementById('main-ui').classList.add('show');
        setTimeout(() => {
          visor.classList.remove('blink');
          visor.classList.add('happy');
          setTimeout(() => visor.classList.remove('happy'), 900);
        }, 300);
      }, 1050);
    }

    function setEyes(x, y) {
      eyeL.style.transform = `translate(${x}px, ${y}px)`;
      eyeR.style.transform = `translate(${x}px, ${y}px)`;
    }

    function start(cmd, x, y) {
      if (activeCmd === cmd) return;
      activeCmd = cmd;
      vib(18);
      setEyes(x, y);
      statusEl.innerText = 'MOVENDO: ' + cmd.toUpperCase().replace('_', ' ');
      fetch('/' + cmd).catch(() => {});
    }

    function stop() {
      if (activeCmd !== null) {
        activeCmd = null;
        setEyes(0, 0);
        statusEl.innerText = 'PARADO';
        fetch('/parar').catch(() => {});
      }
    }

    function triggerAct(act, label) {
      vib(25);
      statusEl.innerText = label;
      if (act === 'feliz') {
        visor.classList.add('happy');
        setTimeout(() => visor.classList.remove('happy'), 1200);
      } else if (act === 'piscar') {
        visor.classList.add('blink');
        setTimeout(() => visor.classList.remove('blink'), 250);
      }
      fetch('/' + act).catch(() => {});
    }

    // Piscar automatico no visor do celular
    setInterval(() => {
      if (isConnected && !activeCmd) {
        visor.classList.add('blink');
        setTimeout(() => visor.classList.remove('blink'), 160);
      }
    }, 3400);

    document.querySelectorAll('.btn').forEach(b => {
      const cmd = b.dataset.cmd;
      const x = parseInt(b.dataset.x || 0);
      const y = parseInt(b.dataset.y || 0);
      if (cmd === 'parar') {
        b.addEventListener('pointerdown', e => { e.preventDefault(); vib(30); stop(); });
        return;
      }
      b.addEventListener('pointerdown', e => {
        e.preventDefault();
        b.classList.add('pressed');
        start(cmd, x, y);
      });
      b.addEventListener('pointerup', e => {
        e.preventDefault();
        b.classList.remove('pressed');
        stop();
      });
      b.addEventListener('pointercancel', e => {
        b.classList.remove('pressed');
        stop();
      });
    });

    window.addEventListener('pointerup', () => {
      document.querySelectorAll('.btn').forEach(b => b.classList.remove('pressed'));
      stop();
    });
  </script>
</body>
</html>
"""

# ------------------------------------------------------------------------------
# 7. SERVIDOR DNS CATIVO (Captive Portal)
# ------------------------------------------------------------------------------
class CaptiveDNS:
    def __init__(self, ip='192.168.4.1'):
        self.ip_bytes = bytes([int(x) for x in ip.split('.')])
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            self.sock.bind(('', 53))
            print("Servidor DNS Cativo ativo na porta 53!")
        except Exception as e:
            print("Aviso ao iniciar DNS Cativo:", e)
            self.sock = None

    def handle(self):
        if not self.sock:
            return
        try:
            while True:
                data, addr = self.sock.recvfrom(512)
                if len(data) < 12:
                    continue
                packet = bytearray()
                packet.extend(data[:2])           # Transaction ID
                packet.extend(b'\x81\x80')        # Flags: Standard query response, No error
                packet.extend(data[4:6])          # Questions count
                packet.extend(data[4:6])          # Answers count
                packet.extend(b'\x00\x00\x00\x00')# Authority RRs, Additional RRs
                
                # Encontra o fim do nome da pergunta
                idx = 12
                while idx < len(data) and data[idx] != 0:
                    idx += 1 + data[idx]
                idx += 5 # Pula byte nulo, QTYPE e QCLASS
                if idx > len(data):
                    continue
                
                packet.extend(data[12:idx])       # Copia a pergunta original
                packet.extend(b'\xc0\x0c')        # Ponteiro para o nome da pergunta
                packet.extend(b'\x00\x01\x00\x01')# Type A, Class IN
                packet.extend(b'\x00\x00\x00\x3c')# TTL: 60s
                packet.extend(b'\x00\x04')        # Data length: 4
                packet.extend(self.ip_bytes)      # IP: 192.168.4.1
                
                self.sock.sendto(packet, addr)
        except OSError:
            pass

dns = CaptiveDNS('192.168.4.1')

# ------------------------------------------------------------------------------
# 8. SERVIDOR WEB HTTP
# ------------------------------------------------------------------------------
import gc
gc.collect()

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(('', 80))
except Exception:
    try:
        import time
        time.sleep_ms(300)
        s.bind(('', 80))
    except Exception as e:
        print("Aviso ao vincular porta 80:", e)

s.listen(3)
s.settimeout(0.2) # Intervalo para animar os olhos enquanto aguarda conexoes

print("Servidor Web pronto! Acesse http://192.168.4.1 no navegador")

# Variáveis de controle de conexão e animação dinâmica dos olhos
conectado = False
last_blink_time = time.ticks_ms()
last_look_time = time.ticks_ms()
last_wait_anim = time.ticks_ms()
wait_step = 0
disconnect_time = None
idle_shift_x = 0
idle_pose_idx = 0
robot_state = "PARADO"

while True:
    dns.handle()
    conn = None
    try:
        try:
            conn, addr = s.accept()
        except OSError:
            agora = time.ticks_ms()
            num_st = check_stations()

            # --- MODO AGUARDANDO BOTÃO "CONECTAR DECABOT" ---
            if not conectado:
                # Mantém na tela de conexão até o usuário clicar em "Conectar Decabot" no celular
                if time.ticks_diff(agora, last_wait_anim) > 400:
                    last_wait_anim = agora
                    wait_step = (wait_step + 1) % 4
                    tela_conexao(wait_step)
                continue

            # --- MODO CONECTADO: MONITORAMENTO DE DESCONEXÃO ---
            if num_st == 0:
                if disconnect_time is None:
                    disconnect_time = agora
                elif time.ticks_diff(agora, disconnect_time) > 4000:
                    # Desconectado há mais de 4s: para motores e volta à tela de conexão
                    conectado = False
                    disconnect_time = None
                    robot_state = "PARADO"
                    parar()
                    wait_step = 0
                    last_wait_anim = agora
                    tela_conexao(0)
                    continue
            else:
                disconnect_time = None

            # --- MODO CONECTADO: ANIMAÇÃO DINÂMICA DOS OLHOS EM REPOUSO ---
            if robot_state == "PARADO":
                # Piscar natural a cada ~2.4 segundos na pose atual
                if time.ticks_diff(agora, last_blink_time) > 2400:
                    piscar_olhos(duplo=(idle_pose_idx % 3 == 0))
                    last_blink_time = time.ticks_ms()

                # Mudar expressão e direção do olhar suavemente a cada ~3.2 segundos
                elif time.ticks_diff(agora, last_look_time) > 3200:
                    idle_pose_idx = (idle_pose_idx + 1) % 8
                    if idle_pose_idx == 0:
                        # Curioso para a esquerda (perspectiva 3D)
                        mover_olhos_suave(-14, -2, "NORMAL", 5, -5, passos=4)
                    elif idle_pose_idx == 1:
                        # Centro atento
                        mover_olhos_suave(0, 0, "NORMAL", 0, 0, passos=3)
                    elif idle_pose_idx == 2:
                        # Curioso para a direita (perspectiva 3D)
                        mover_olhos_suave(14, -2, "NORMAL", -5, 5, passos=4)
                    elif idle_pose_idx == 3:
                        # Olhinhos felizes sorrindo (^ ^)
                        mover_olhos_suave(0, -2, "FELIZ", 2, 2, passos=3)
                    elif idle_pose_idx == 4:
                        # Olhando para cima curioso
                        mover_olhos_suave(0, -7, "SURPRESO", 0, 0, passos=3)
                    elif idle_pose_idx == 5:
                        # Centro + piscadinha de um olho só ;)
                        mover_olhos_suave(0, 0, "NORMAL", 0, 0, passos=3)
                        piscar_um_olho()
                    elif idle_pose_idx == 6:
                        # Olhar focado de canto
                        mover_olhos_suave(-10, 3, "FOCADO", 3, -3, passos=3)
                    else:
                        # Volta para o centro
                        mover_olhos_suave(0, 0, "NORMAL", 0, 0, passos=3)
                    last_look_time = time.ticks_ms()
            continue

        # --- CONEXÃO HTTP RECEBIDA ---
        disconnect_time = None

        # Dá tempo ao celular para transferir a página sem timeout prematuro
        conn.settimeout(3.0)
        raw_req = conn.recv(1024)
        if not raw_req:
            conn.close()
            continue
            
        request = raw_req.decode('utf-8')
        
        # Extrai a rota solicitada (ex: "/conectar", "/frente", "/parar", "/")
        try:
            path = request.split()[1]
        except Exception:
            path = "/"

        # Quando o usuário clica no botão grande "Conectar Decabot"
        if path == "/conectar":
            conn.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
            conn.close()
            conn = None
            if not conectado:
                conectado = True
                animar_acordar()
                last_blink_time = time.ticks_ms()
                last_look_time = time.ticks_ms()
            continue

        is_action = True
        if path == "/frente_esq":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = -1
            frente_esq()
        elif path == "/frente_dir":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = 1
            frente_dir()
        elif path == "/frente":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = 0
            frente()
        elif path == "/tras_esq":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = -1
            tras_esq()
        elif path == "/tras_dir":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = 1
            tras_dir()
        elif path == "/tras":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = 0
            tras()
        elif path == "/esquerda":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = -1
            esquerda()
        elif path == "/direita":
            conectado = True
            robot_state = "MOVENDO"
            idle_shift_x = 1
            direita()
        elif path == "/parar":
            robot_state = "PARADO"
            idle_shift_x = 0
            if conectado:
                parar()
            last_blink_time = time.ticks_ms()
        elif path == "/feliz":
            conectado = True
            mover_olhos_suave(0, -3, "FELIZ", 2, 2, passos=3)
            beep(880, 60)
            beep(1175, 90)
            last_look_time = time.ticks_ms()
        elif path == "/piscar":
            conectado = True
            piscar_um_olho()
            last_blink_time = time.ticks_ms()
        elif path == "/bip":
            beep(987, 100)
        else:
            is_action = False
        
        if is_action:
            conn.send('HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        elif path in ("/generate_204", "/gen_204", "/hotspot-detect.html", "/ncsi.txt", "/connecttest.txt"):
            # Captive Portal probe: redireciona para a pagina inicial do controle
            conn.send('HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        else:
            conn.send('HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n')
            conn.sendall(HTML_PAGE)
        conn.close()
    except Exception as e:
        if conn:
            try:
                conn.close()
            except:
                pass

