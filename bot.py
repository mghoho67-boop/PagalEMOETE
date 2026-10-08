import sys
import asyncio
import httpx
import requests
import json
import time
import struct
import random
from datetime import datetime, timedelta
from typing import Optional, Dict

from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import Online_pb2
import os
import re
import ast
import importlib.util
from google.protobuf import descriptor_pb2

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

# ── Auto message config ───────────────────────────────────────────────────────
AUTO_MESSAGE = True          # False garepachi auto-msg band
MIN_DELAY = 45               # seconds (slow + safe)
MAX_DELAY = 120              # seconds (slow + safe)
MESSAGES_FILE = "messages.txt"

player_online = False
connection_closed = True
shutdown_requested = False
chat_writer = None
_chat_write_lock = None  # lazy init — event loop ready huna parchha

_ALL_TASKS: set = set()


def _get_chat_lock():
    global _chat_write_lock
    if _chat_write_lock is None:
        _chat_write_lock = asyncio.Lock()
    return _chat_write_lock

headers = {
    'X-GA': "v1 1",
    'Expect': "100-continue",
    'Accept-Encoding': "gzip",
    'Connection': "Keep-Alive",
    'X-Unity-Version': "2018.4.11f1",
    'Content-Type': "application/octet-stream",
    'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 11; ASUS_Z01QD Build/PI)"
}


def print_success(text):
    print(f"[+] {text}")

def print_error(text):
    print(f"[-] {text}")

def print_warning(text):
    print(f"[!] {text}")

def print_info(text):
    print(f"[i] {text}")

def print_debug(text):
    print(f"[DEBUG] {text}")


async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type == "varint":
            field_data["data"] = result.data
        elif result.wire_type == "string":
            field_data["data"] = result.data
        elif result.wire_type == "bytes":
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            field_data["data"] = await parse_results(result.data.results)
        result_dict[result.field] = field_data
    return result_dict


async def decode_protobuf(data):
    from protobuf_decoder.protobuf_decoder import Parser
    parsed_results = Parser().parse(data)
    parsed_results_dict = await parse_results(parsed_results)
    return json.dumps(parsed_results_dict)


def _flatten_node(node):
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if isinstance(v, dict):
            if "data" in v and len(v) <= 2:
                out[str(k)] = _flatten_node(v["data"])
            else:
                out[str(k)] = _flatten_node(v)
        else:
            out[str(k)] = v
    return out


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            b |= 0x80
        out.append(b)
        if not n:
            break
    return bytes(out)


def _length_delimited(field, value):
    if isinstance(value, str):
        value = value.encode()
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _varint_field(field, value):
    return _varint((field << 3) | 0) + _varint(value)


def _serialize(fields):
    out = bytearray()
    for k, v in fields.items():
        if isinstance(v, dict):
            out.extend(_length_delimited(k, _serialize(v)))
        elif isinstance(v, int):
            out.extend(_varint_field(k, v))
        elif isinstance(v, (str, bytes)):
            out.extend(_length_delimited(k, v))
    return bytes(out)


ACCEPT_INVITE_FIELD9_1_HEX = (
    "08FBA33B0522052002041B0222220007000C00010004000547E265AE1080779546762514110104076fa2e8770e748c3f6a68ed75000000ff08080500cacfa16d"
)

ACCEPT_INVITE_FIELD9_11_HEX = (
    "0362625351363749545a306f50416456324b796f566c664b314a522b4c336737676d2f597a6f7064736278516b676d6f3275696a4e5a512f6445356778643942356c6f69644867315574306e34573146634c382b624a69436c61532b305a625070547a32574f302b436d497067325958347a4d38722f526a4a735371764a334a357a69756950676b5a35642b386a7576664367547a7135776e78756b4d5a734d63745544582b666a65325373565833542b6950676c4f31474736526e66383661754e7945786c4a6942316a656d59636452595459496c65612f68757353657a6c6753424c49714e64482f584b7876507a6c74417461756b53746d50414b4c5679544b42337536685146456b7635584962534f626f6f436b4b357a614e523736746e6e6b576176776748446e73426770656c75506e6652644e75714c43547a5553707569355a77425448653937667465354c3432365a71554441724933565a3636687030546234384c756730562b366c514d66636e467143486d33436a356f5549395369776934424758454e33317436786c794932416f6b2f4a322f2b355255574d3645774c497338306a632f55317765745a6b457639524e496f70563275726d38324e4a4858757166346e4d45785a516e6879766268334e5a68554a3431427934574a4b6f44656d367864733873474e444e5874472b4a68466976714d683235536139744b354462336f416871463769416675472f336535304e44744e4e3643796a794a5370633334784765696f6165533544597663782b77634c656e4c373945517475547477716d7a4b4d7575536268514635785272717978796538452f7a5563706a7739534d58654b5268394b685252666b6f4f51785741673d3d"
)


def _build_accept_invite_proto(owner_uid, code_str, game_version="1.132.1"):
    fields = {
        1: 4,
        2: {
            1: int(owner_uid),
            3: int(owner_uid),
            4: bytes.fromhex("0107090a0b120f19202729"),
            8: 1,
            9: {
                1: ACCEPT_INVITE_FIELD9_1_HEX,
                2: 235,
                3: {14: 80, 11: 86},
                4: "{Y\\R",
                6: 13,
                7: {2: 4},
                8: str(game_version),
                9: 2,
                10: 1,
                11: bytes.fromhex(ACCEPT_INVITE_FIELD9_11_HEX),
            },
            10: str(code_str),
            11: {1: "IDC3", 2: 107, 3: "BD"},
            13: "en",
            16: "374f5219",
            20: {1: 21},
            23: "a_6534489873065906362",
            24: "https://dl-sg-production.freefiremobile.com/D5A73B5E05EC88BB7181_7104104913_107_1767710870_0",
            27: {1: 2, 2: 4},
        },
    }
    return _serialize(fields)


def _wrap_0515(proto_bytes, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(proto_bytes, AES.block_size)).hex()
    length = len(encrypted) // 2
    hex_length = hex(length)[2:]
    if len(hex_length) == 2:
        header = "0515000000"
    elif len(hex_length) == 3:
        header = "051500000"
    elif len(hex_length) == 4:
        header = "05150000"
    elif len(hex_length) == 5:
        header = "0515000"
    else:
        header = "0515000000"
    return bytes.fromhex(header + hex_length + encrypted)


async def send_accept_invite_packet(owner_uid, code_str, key, iv, writer, game_version="1.132.1"):
    try:
        if writer is None or writer.is_closing():
            return False
        proto_bytes = _build_accept_invite_proto(int(owner_uid), str(code_str), game_version)
        final_packet = _wrap_0515(proto_bytes, key, iv)
        writer.write(final_packet)
        await writer.drain()
        return True
    except Exception as e:
        print_error(f"send_accept_invite_packet error: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-MESSAGE (clan chat)
# ══════════════════════════════════════════════════════════════════════════════

def _wrap_packet(proto_bytes, prefix_hex, key, iv):
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(proto_bytes, AES.block_size))
    return bytes.fromhex(prefix_hex) + len(enc).to_bytes(4, "big") + enc


def load_pb2_descriptor(filename):
    """mg24_proto/<filename> lai IMPORT nagari, bhitra ko descriptor padhchha
    (Online_pb2 sanga name conflict nahos bhanera)."""
    spec = importlib.util.find_spec("mg24_proto")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("mg24-proto install chhaina: pip install mg24-proto")
    path = os.path.join(list(spec.submodule_search_locations)[0], filename)
    src = open(path, encoding="utf-8").read()
    m = re.search(r"AddSerializedFile\((b'(?:[^'\\]|\\.)*'|b\"(?:[^\"\\]|\\.)*\")\)", src, re.S)
    if not m:
        raise RuntimeError(f"{filename} descriptor fela paren")
    fdp = descriptor_pb2.FileDescriptorProto()
    fdp.ParseFromString(ast.literal_eval(m.group(1)))
    return fdp


_LOGIN_FIELDS = None


def get_login_field_numbers():
    """mg24_proto/PorTs_pb2.py lai IMPORT nagari, tyo bhitra ko descriptor bata
    GetLoginData ko field name -> number nikalchha (Online_pb2 sanga conflict hudaina)."""
    global _LOGIN_FIELDS
    if _LOGIN_FIELDS is not None:
        return _LOGIN_FIELDS
    fdp = load_pb2_descriptor("PorTs_pb2.py")
    for msg in fdp.message_type:
        if msg.name == "GetLoginData":
            _LOGIN_FIELDS = {f.name: f.number for f in msg.field}
            return _LOGIN_FIELDS
    raise RuntimeError("GetLoginData message fela paren")


def _pb_field(tree, num):
    """Wlk() tree bata field number anusar value (str / bytes / int)."""
    if num + 1000 in tree:
        raw = bytes.fromhex(tree[num + 1000][0])
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
    v = tree.get(num)
    return v[0] if v else None


# ══════════════════════════════════════════════════════════════════════════════
#  AUTH PACKETS  ← FIXED
# ══════════════════════════════════════════════════════════════════════════════
def build_auth_0115(account_id, token, timestamp, key, iv):
    """Chat TCP auth — 6d19 + uid(8B) + ts(4B) + len(4B) + AES-CBC(token)."""
    if isinstance(key, str): key = bytes.fromhex(key)
    if isinstance(iv,  str): iv  = bytes.fromhex(iv)
    enc = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(token.encode(), 16))
    return (
        "6d19"
        + format(int(account_id), "x").zfill(16)
        + int(timestamp).to_bytes(4, "big").hex()
        + len(enc).to_bytes(4, "big").hex()
        + enc.hex()
    )


def build_clan_auth_packet(clan_id, clan_data, key, iv):
    if not isinstance(clan_data, (bytes, str)):
        clan_data = str(clan_data)
    fields = {1: 3, 2: {1: int(clan_id), 2: 1, 4: clan_data}}
    return _wrap_packet(_serialize(fields), "1201", key, iv)


def _next_saturday_6am():
    now = datetime.utcnow()
    dt = (now + timedelta(days=(7 - now.weekday()))).replace(hour=6, minute=0, second=0, microsecond=0)
    return int(dt.timestamp())


def build_msg_packet(msg, tp, target_id, key, iv):
    """asal library (Group_aenhaamdtsmodz.send_message) bata verbatim -- clan/private/squad sabai yehi ho.
    tp=1 clan chat, tp=2 private whisper, tp=None/0 squad chat (field 3 omit huncha)."""
    account_id = _bot_identity["uid"] or int(target_id)
    account_name = _bot_identity["name"] or "Bot"
    region = _bot_identity["region"] or "SG"
    fields = {1: int(account_id), 2: int(target_id)}
    if tp:
        fields[3] = int(tp)
    fields[4] = msg
    fields[5] = int(time.time())
    fields[9] = {
        1: account_name, 2: random.choice([902000126, 902000154, 902000003, 902027018]),
        3: 901027033, 4: 228, 10: 11, 11: 101,
        13: {1: 2},
        14: {1: int(account_id), 2: 8,
             3: bytes([15, 6, 21, 8, 10, 11, 19, 12, 17, 4, 14, 20, 7, 2, 1, 5, 16, 3, 13, 18])},
    }
    fields[10] = region.lower()
    fields[13] = {2: 1, 3: 1}
    fields[14] = {1: {1: 1, 2: 1, 3: random.randint(1, 5), 4: 1, 5: _next_saturday_6am(), 6: region}}
    body = _serialize(fields)
    body = bytes([0x08, 0x01, 0x12]) + _varint(len(body)) + body
    return _wrap_packet(body, _squad_prefix(0x12, region), key, iv)


def load_messages(path):
    msgs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    msgs.append(line)
    except FileNotFoundError:
        print_error(f"'{path}' not found — using fallback")
    except Exception as e:
        print_error(f"messages load error: {e}")
    return msgs or ["[00FF00]Hello Everyone!", "[FFFF00]Welcome!"]


async def auto_message_scheduler(key, iv, clan_id):
    global chat_writer
    counter, last = 0, None
    print_info(f"Auto-msg started | {MIN_DELAY}-{MAX_DELAY}s")
    while not shutdown_requested:
        try:
            if chat_writer is None or chat_writer.is_closing():
                await asyncio.sleep(2)
                continue

            messages = load_messages(MESSAGES_FILE)  # reloads each cycle
            pool = [m for m in messages if m != last] or messages
            chosen = random.choice(pool)

            pkt = build_msg_packet(chosen, 1, clan_id, key, iv)
            sent = False
            for _try in range(3):
                async with _get_chat_lock():
                    if chat_writer is None or chat_writer.is_closing():
                        break
                    try:
                        chat_writer.write(pkt)
                        await chat_writer.drain()
                        sent = True
                        break
                    except Exception as we:
                        print_warning(f"auto-msg write try{_try+1}: {we}")
                await asyncio.sleep(1.5)
            if not sent:
                print_warning("auto-msg skipped: chat socket not ready")
                await asyncio.sleep(8)
                continue

            counter += 1
            last = chosen
            delay = random.randint(MIN_DELAY, MAX_DELAY)
            print(f"[AUTO-MSG] #{counter} {chosen[:60]} | next {delay}s")
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print_error(f"auto-msg error: {e}")
            await asyncio.sleep(10)


# ══════════════════════════════════════════════════════════════════════════════
#  SQUAD CHAT PACKET
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  CHAT COMMANDS: /5  /6  /e
#  (SQuAd size 5/6 kholera invite pathaune, ra emote khelaune)
# ══════════════════════════════════════════════════════════════════════════════

EMOTES_BY_NUMBER = {
    1: 909000001,
    2: 909000002,
    3: 909000003,
    4: 909000004,
    5: 909000005,
    6: 909000006,
    7: 909000007,
    8: 909049010,
    9: 909051003,
    10: 909033002,
    11: 909041005,
    12: 909038010,
    13: 909039011,
    14: 909040010,
    15: 909000081,
    16: 909000085,
    17: 909000063,
    18: 909000075,
    19: 909033001,
    20: 909000090,
    21: 909000068,
    22: 909000098,
    23: 909035007,
    24: 909037011,
    25: 909038012,
    26: 909035012,
    27: 909042008,
    28: 909000055,
    29: 909000045,
    30: 909000010,
    31: 909000014,
    32: 909000034,
    33: 909000039,
    34: 909000072,
    35: 909036001,
    36: 909050020,
    37: 909050013,
    38: 909000008,
    39: 909000009,
    40: 909000012,
    41: 909000015,
    42: 909000025,
    43: 909000032,
    44: 909000040,
    45: 909000041,
    46: 909050008,
    47: 909047015,
    48: 909047018,
    49: 909047019,
    50: 909050005,
    51: 909051014,
    52: 909050009,
    53: 909051013,
    54: 909051012,
    55: 909051010,
    56: 909051004,
    57: 909051002,
    58: 909051001,
    59: 909048015,
    60: 909044015,
    61: 909041008,
    62: 909049003,
    63: 909049001,
    64: 909041013,
    65: 909050014,
    66: 909050015,
    67: 909050002,
    68: 909042007,
    69: 909050028,
    70: 909049012,
    71: 909038004,
    72: 909034001,
    73: 909049017,
    74: 909040004,
    75: 909041003,
    76: 909000011,
    77: 909000013,
    78: 909000016,
    79: 909000017,
    80: 909000018,
    81: 909000019,
    82: 909000020,
    83: 909000021,
    84: 909000022,
    85: 909000023,
    86: 909000024,
    87: 909000026,
    88: 909000027,
    89: 909000028,
    90: 909000029,
    91: 909000031,
    92: 909000033,
    93: 909000035,
    94: 909000036,
    95: 909000037,
    96: 909000038,
    97: 909000042,
    98: 909000043,
    99: 909000044,
    100: 909000046,
    101: 909000047,
    102: 909000048,
    103: 909000049,
    104: 909000051,
    105: 909000052,
    106: 909000053,
    107: 909000054,
    108: 909000079,
    109: 909000080,
    110: 909000082,
    111: 909000083,
    112: 909000084,
    113: 909000086,
    114: 909000087,
    115: 909000088,
    116: 909000089,
    117: 909000091,
    118: 909000092,
    119: 909000093,
    120: 909000094,
    121: 909000095,
    122: 909000096,
    123: 909000097,
    124: 909000099,
    125: 909000100,
    126: 909000101,
    127: 909000102,
    128: 909000103,
    129: 909000104,
    130: 909000105,
    131: 909000106,
    132: 909000107,
    133: 909000121,
    134: 909000122,
    135: 909000123,
    136: 909000124,
    137: 909000125,
    138: 909000126,
    139: 909000127,
    140: 909000128,
    141: 909000129,
    142: 909000130,
    143: 909000131,
    144: 909000132,
    145: 909000133,
    146: 909000134,
    147: 909000135,
    148: 909000136,
    149: 909000137,
    150: 909000138,
    151: 909000139,
    152: 909000140,
    153: 909000141,
    154: 909000142,
    155: 909000143,
    156: 909000144,
    157: 909000145,
    158: 909000150,
    159: 909033003,
    160: 909033004,
    161: 909033005,
    162: 909033006,
    163: 909033007,
    164: 909033008,
    165: 909033009,
    166: 909033010,
    167: 909034002,
    168: 909034003,
    169: 909034004,
    170: 909034005,
    171: 909034006,
    172: 909034007,
    173: 909034008,
    174: 909034009,
    175: 909034010,
    176: 909034011,
    177: 909034012,
    178: 909034013,
    179: 909034014,
    180: 909035001,
    181: 909035002,
    182: 909035003,
    183: 909035004,
    184: 909035005,
    185: 909035006,
    186: 909035008,
    187: 909035009,
    188: 909035010,
    189: 909035011,
    190: 909035013,
    191: 909035014,
    192: 909035015,
    193: 909036002,
    194: 909036003,
    195: 909036004,
    196: 909036005,
    197: 909036006,
    198: 909036008,
    199: 909036009,
    200: 909036010,
    201: 909036011,
    202: 909036012,
    203: 909036014,
    204: 909037001,
    205: 909037002,
    206: 909037003,
    207: 909037004,
    208: 909037005,
    209: 909037006,
    210: 909037007,
    211: 909037008,
    212: 909037009,
    213: 909037010,
    214: 909037012,
    215: 909038001,
    216: 909038002,
    217: 909038003,
    218: 909038005,
    219: 909038006,
    220: 909038008,
    221: 909038009,
    222: 909038011,
    223: 909038013,
    224: 909039001,
    225: 909039002,
    226: 909039003,
    227: 909039004,
    228: 909039005,
    229: 909039006,
    230: 909039007,
    231: 909039008,
    232: 909039009,
    233: 909039010,
    234: 909039012,
    235: 909039013,
    236: 909039014,
    237: 909040001,
    238: 909040002,
    239: 909040003,
    240: 909040005,
    241: 909040006,
    242: 909040008,
    243: 909040009,
    244: 909040011,
    245: 909040012,
    246: 909040013,
    247: 909041001,
    248: 909041002,
    249: 909041004,
    250: 909041006,
    251: 909041007,
    252: 909041009,
    253: 909041010,
    254: 909041011,
    255: 909041012,
    256: 909041014,
    257: 909041015,
    258: 909042001,
    259: 909042002,
    260: 909042003,
    261: 909042004,
    262: 909042005,
    263: 909042006,
    264: 909042009,
    265: 909042011,
    266: 909042012,
    267: 909042013,
    268: 909042016,
    269: 909042018,
    270: 909043001,
    271: 909043002,
    272: 909043003,
    273: 909043004,
    274: 909043005,
    275: 909043006,
    276: 909043007,
    277: 909043008,
    278: 909043009,
    279: 909043010,
    280: 909043013,
    281: 909044001,
    282: 909044002,
    283: 909044003,
    284: 909044004,
    285: 909044005,
    286: 909044006,
    287: 909044007,
    288: 909044008,
    289: 909044009,
    290: 909044010,
    291: 909044011,
    292: 909044012,
    293: 909044016,
    294: 909045001,
    295: 909045002,
    296: 909045003,
    297: 909045004,
    298: 909045005,
    299: 909045006,
    300: 909045007,
    301: 909045008,
    302: 909045009,
    303: 909045010,
    304: 909045011,
    305: 909045012,
    306: 909045015,
    307: 909045016,
    308: 909045017,
    309: 909046001,
    310: 909046002,
    311: 909046003,
    312: 909046004,
    313: 909046005,
    314: 909046006,
    315: 909046007,
    316: 909046008,
    317: 909046009,
    318: 909046010,
    319: 909046011,
    320: 909046012,
    321: 909046013,
    322: 909046014,
    323: 909046015,
    324: 909046016,
    325: 909046017,
    326: 909047001,
    327: 909047002,
    328: 909047003,
    329: 909047004,
    330: 909047005,
    331: 909047006,
    332: 909047007,
    333: 909047008,
    334: 909047009,
    335: 909047010,
    336: 909047011,
    337: 909047012,
    338: 909047013,
    339: 909047016,
    340: 909047017,
    341: 909048001,
    342: 909048002,
    343: 909048003,
    344: 909048004,
    345: 909048005,
    346: 909048006,
    347: 909048007,
    348: 909048008,
    349: 909048009,
    350: 909048010,
    351: 909048011,
    352: 909048012,
    353: 909048013,
    354: 909048014,
    355: 909048016,
    356: 909048017,
    357: 909048018,
    358: 909049002,
    359: 909049004,
    360: 909049005,
    361: 909049006,
    362: 909049007,
    363: 909049008,
    364: 909049009,
    365: 909049011,
    366: 909049013,
    367: 909049014,
    368: 909049015,
    369: 909049016,
    370: 909049018,
    371: 909049019,
    372: 909049020,
    373: 909049021,
    374: 909050003,
    375: 909050004,
    376: 909050006,
    377: 909050010,
    378: 909050011,
    379: 909050012,
    380: 909050016,
    381: 909050017,
    382: 909050018,
    383: 909050019,
    384: 909050021,
}

EMOTES_BY_NAME = {
    "hello": 909000001,
    "lol": 909000002,
    "provoke": 909000003,
    "applause": 909000004,
    "dab": 909000005,
    "chicken": 909000006,
    "armwave": 909000007,
    "p90": 909049010,
    "m60": 909051003,
    "mp5": 909033002,
    "groza": 909041005,
    "thompson_evo": 909038010,
    "m10_red": 909039011,
    "mp40_blue": 909040010,
    "m10_green": 909000081,
    "xm8": 909000085,
    "ak": 909000063,
    "mp40": 909000075,
    "m4a1": 909033001,
    "famas": 909000090,
    "scar": 909000068,
    "ump": 909000098,
    "m18": 909035007,
    "fist": 909037011,
    "g18": 909038012,
    "an94": 909035012,
    "woodpecker": 909042008,
    "money": 909000055,
    "heart": 909000045,
    "rose": 909000010,
    "throne": 909000014,
    "pirate": 909000034,
    "car": 909000039,
    "cobra": 909000072,
    "ghost": 909036001,
    "sholay": 909050020,
    "blade": 909050013,
    "dance": 909000008,
    "babyshark": 909000009,
    "pushup": 909000012,
    "dragon": 909000015,
    "highfive": 909000025,
    "selfie": 909000032,
    "breakdance": 909000040,
    "kungfu": 909000041,
    "thor": 909050008,
    "rasengan": 909047015,
    "ninja": 909047018,
    "clone": 909047019,
    "fireball": 909050005,
    "puffyride": 909051014,
    "circle": 909050009,
    "petals": 909051013,
    "bow": 909051012,
    "motorbike": 909051010,
    "shower": 909051004,
    "dream": 909051002,
    "angelic": 909051001,
    "paint": 909048015,
    "sword": 909044015,
    "flar": 909041008,
    "owl": 909049003,
    "bigdill": 909049001,
    "csgm": 909041013,
    "mapread": 909050014,
    "tomato": 909050015,
    "ninjasummon": 909050002,
    "100l": 909042007,
    "auraboat": 909050028,
    "flyingguns": 909049012,
    "valentineheart": 909038004,
    "rampagebook": 909034001,
    "guildflag": 909049017,
    "fish": 909040004,
    "inosuke": 909041003,
    "mummydance": 909000011,
    "shuffling": 909000013,
    "dangerousgame": 909000016,
    "jaguardance": 909000017,
    "threaten": 909000018,
    "shakewithme": 909000019,
    "devilsmove": 909000020,
    "furiousslam": 909000021,
    "moonflip": 909000022,
    "wigglewalk": 909000023,
    "battledance": 909000024,
    "shakeitup": 909000026,
    "gloriousspin": 909000027,
    "cranekick": 909000028,
    "partydance": 909000029,
    "jigdance": 909000031,
    "soulshaking": 909000033,
    "healingdance": 909000035,
    "topdj": 909000036,
    "deathglare": 909000037,
    "powerofmoney": 909000038,
    "bonappetit": 909000042,
    "aimfire": 909000043,
    "swan": 909000044,
    "teatime": 909000046,
    "bringiton": 909000047,
    "whyohwhy": 909000048,
    "fancyhands": 909000049,
    "shimmy": 909000051,
    "doggie": 909000052,
    "challengeon": 909000053,
    "lasso": 909000054,
    "morepractice": 909000079,
    "ffws2021": 909000080,
    "goodgame": 909000082,
    "greetings": 909000083,
    "walker": 909000084,
    "mythosfour": 909000086,
    "championgrab": 909000087,
    "winandchill": 909000088,
    "hadouken": 909000089,
    "bigsmash": 909000091,
    "fancysteps": 909000092,
    "allincontrol": 909000093,
    "debugging": 909000094,
    "waggorwave": 909000095,
    "crazyguitar": 909000096,
    "poof": 909000097,
    "challenger": 909000099,
    "partygame5": 909000100,
    "partygame6": 909000101,
    "partygame3": 909000102,
    "partygame4": 909000103,
    "partygame7": 909000104,
    "partygame1": 909000105,
    "partygame8": 909000106,
    "partygame2": 909000107,
    "dribbleking": 909000121,
    "ffwsguitar": 909000122,
    "mindit": 909000123,
    "goldencombo": 909000124,
    "sickmoves": 909000125,
    "rapswag": 909000126,
    "battleinstyle": 909000127,
    "rulersflag": 909000128,
    "moneythrow": 909000129,
    "endlessbullets": 909000130,
    "smoothsway": 909000131,
    "number1": 909000132,
    "fireslam": 909000133,
    "heartbroken": 909000134,
    "rockpaperscissors": 909000135,
    "shatteredreality": 909000136,
    "haloofmusic": 909000137,
    "burntbbq": 909000138,
    "switchingsteps": 909000139,
    "creedslay": 909000140,
    "leapoffail": 909000141,
    "rhythmgirl": 909000142,
    "helicoptership": 909000143,
    "kungfutigers": 909000144,
    "possessedwarrior": 909000145,
    "raiseyourthumb": 909000150,
    "comeanddance": 909033003,
    "dropkick": 909033004,
    "sitdown": 909033005,
    "booyahsparks": 909033006,
    "ffwsdance": 909033007,
    "easypeasy": 909033008,
    "winnerthrow": 909033009,
    "weightofvictory": 909033010,
    "collapse": 909034002,
    "flaminggroove": 909034003,
    "energetic": 909034004,
    "ridicule": 909034005,
    "teasewaggor": 909034006,
    "greatconductor": 909034007,
    "fakedeath": 909034008,
    "twerk": 909034009,
    "brheroic": 909034010,
    "brmaster": 909034011,
    "csheroic": 909034012,
    "csmaster": 909034013,
    "yesido": 909034014,
    "freemoney": 909035001,
    "singersb03": 909035002,
    "singersb0203": 909035003,
    "singersb010203": 909035004,
    "victoriouseagle": 909035005,
    "flyingsaucer": 909035006,
    "bobbledance": 909035008,
    "weighttraining": 909035009,
    "beautifullove": 909035010,
    "groovemoves": 909035011,
    "louderplease": 909035013,
    "ninjastand": 909035014,
    "creatorinaction": 909035015,
    "shibasurf": 909036002,
    "waiterwalk": 909036003,
    "grafficameraman": 909036004,
    "agileboxer": 909036005,
    "sunbathing": 909036006,
    "skateboardswag": 909036008,
    "phantomtamer": 909036009,
    "signal": 909036010,
    "eternaldescent": 909036011,
    "swaggydance": 909036012,
    "admire": 909036014,
    "reindeerfloat": 909037001,
    "bamboodance": 909037002,
    "constellationdance": 909037003,
    "trophygrab": 909037004,
    "starryhands": 909037005,
    "yum": 909037006,
    "happydancing": 909037007,
    "juggle": 909037008,
    "neonsign": 909037009,
    "beasttease": 909037010,
    "clapdance": 909037012,
    "influencer": 909038001,
    "macarena": 909038002,
    "technoblast": 909038003,
    "angrywalk": 909038005,
    "makesomenoise": 909038006,
    "crocohooray": 909038008,
    "scorpionspin": 909038009,
    "shallwedance": 909038011,
    "spinmaster": 909038013,
    "festival": 909039001,
    "artisticdance": 909039002,
    "forwardbackward": 909039003,
    "scorpionfriend": 909039004,
    "achingpower": 909039005,
    "earthlyforce": 909039006,
    "grenademagic": 909039007,
    "ohyeah": 909039008,
    "graceonwheels": 909039009,
    "flex": 909039010,
    "firebeasttamer": 909039012,
    "crimsontunes": 909039013,
    "swaggyvsteps": 909039014,
    "chromaticfinish": 909040001,
    "smashthefeather": 909040002,
    "sonoroussteps": 909040003,
    "chromaticpop": 909040005,
    "chromatwist": 909040006,
    "birthofjustice": 909040008,
    "spidersense": 909040009,
    "playwiththunderbolt": 909040011,
    "anniversary": 909040012,
    "wisdomswing": 909040013,
    "thunderflash": 909041001,
    "whirlpool": 909041002,
    "flyinginksword": 909041004,
    "dancepuppet": 909041006,
    "highknees": 909041007,
    "feeltheelectricity": 909041009,
    "whacacotton": 909041010,
    "honorablemention": 909041011,
    "brgrandmaster": 909041012,
    "monsterclubbing": 909041014,
    "basudaradance": 909041015,
    "stirfryfrostfire": 909042001,
    "moneyrain": 909042002,
    "frostfirecalling": 909042003,
    "stompingfoot": 909042004,
    "thisway": 909042005,
    "excellentservice": 909042006,
    "celebrationschuss": 909042009,
    "dawnvoyage": 909042011,
    "lamborghiniride": 909042012,
    "toiletman": 909042013,
    "handgrooves": 909042016,
    "kemusan": 909042018,
    "ribbitrider": 909043001,
    "innerself": 909043002,
    "emperortreasure": 909043003,
    "whysochaos": 909043004,
    "hugefeast": 909043005,
    "colorburst": 909043006,
    "dragonswipe": 909043007,
    "samba": 909043008,
    "speedsummon": 909043009,
    "whatamatch": 909043010,
    "whatapair": 909043013,
    "bytemounting": 909044001,
    "unicyclist": 909044002,
    "basketrafting": 909044003,
    "happylamb": 909044004,
    "paradox": 909044005,
    "harmoniousparadox": 909044006,
    "raiseyourthumb2": 909044007,
    "claphands": 909044008,
    "donedeal": 909044009,
    "starcatcher": 909044010,
    "paradoxwings": 909044011,
    "zombified": 909044012,
    "honkup": 909044016,
    "cyclone": 909045001,
    "springrocker": 909045002,
    "giddyup": 909045003,
    "goosydance": 909045004,
    "captainvictor": 909045005,
    "youknowimgood": 909045006,
    "stepstep": 909045007,
    "superyay": 909045008,
    "moonwalk": 909045009,
    "flowersalute": 909045010,
    "foxyrun": 909045011,
    "waggorsseesaw": 909045012,
    "floatingmeditation": 909045015,
    "naatunaatu": 909045016,
    "championswalk": 909045017,
    "auraboarder": 909046001,
    "booyahchamp": 909046002,
    "controlledcombustion": 909046003,
    "cheerstovictory": 909046004,
    "shoeshining": 909046005,
    "gunspinning": 909046006,
    "crowdpleaser": 909046007,
    "nosweat": 909046008,
    "magmaquake": 909046009,
    "maxfirepower": 909046010,
    "canttouchthis": 909046011,
    "firestarter": 909046012,
    "ffwsflag": 909046013,
    "beatdrop": 909046014,
    "spatialawareness": 909046015,
    "trapping": 909046016,
    "soaringup": 909046017,
    "wontbowdown": 909047001,
    "aurora": 909047002,
    "couchfortwo": 909047003,
    "flutterdash": 909047004,
    "slipperythrone": 909047005,
    "acceptancespeech": 909047006,
    "lovemelovemenot": 909047007,
    "scissorsavvy": 909047008,
    "thinker": 909047009,
    "matchcountdown": 909047010,
    "hiptwists": 909047011,
    "jkt48": 909047012,
    "stormyascent": 909047013,
    "thousandyears": 909047016,
    "ninjasign": 909047017,
    "rescue": 909048001,
    "midnightperuse": 909048002,
    "guitargroove": 909048003,
    "keyboardplayer": 909048004,
    "ondrums": 909048005,
    "chacchac": 909048006,
    "pillowfight": 909048007,
    "targetpractice": 909048008,
    "goofycamel": 909048009,
    "hitasix": 909048010,
    "flagsummon": 909048011,
    "swiftsteps": 909048012,
    "carnivalfunk": 909048013,
    "slurp": 909048014,
    "halftime": 909048016,
    "throwin": 909048017,
    "bailalorocky": 909048018,
    "handraise": 909049002,
    "slapandtwist": 909049004,
    "sidewiggle": 909049005,
    "creationdays": 909049006,
    "rainingcoins": 909049007,
    "clapclaphooray": 909049008,
    "infiniteloops": 909049009,
    "boxingmachine": 909049011,
    "comicbarf": 909049013,
    "driveby": 909049014,
    "pedalmetal": 909049015,
    "spearspin": 909049016,
    "discodazzle": 909049018,
    "squatchallenge": 909049019,
    "winninggoal": 909049020,
    "headhigh": 909049021,
    "finalbattle": 909050003,
    "foreheadpoke": 909050004,
    "flyingraijin": 909050006,
    "drumtwirl": 909050010,
    "bunnyaction": 909050011,
    "broomswoosh": 909050012,
    "tacticalmoveout": 909050016,
    "bunnywiggle": 909050017,
    "flamingheart": 909050018,
    "rainorshine": 909050019,
    "peakpoints": 909050021,
}


def _find_emote(arg):
    """/e 1 (number), /e ak (name) -- dubai chalcha, case-insensitive."""
    arg = arg.strip()
    if arg.isdigit():
        return EMOTES_BY_NUMBER.get(int(arg))
    return EMOTES_BY_NAME.get(arg.lower())


BAN_CHECK_API = "https://amin-team-api.vercel.app/check_banned?player_id={}"
INFO_API = "https://info-apiff.vercel.app/info?uid={}"
DUO_API = "https://api-free-fire-dou-info-by-ckrpro.vercel.app/api/duo?uid={}"


COLOR_CODES_TEXT = (
    "[C][B][FFD700]COLOR CODES\n"
    "[FF0000]RED: FF0000\n"
    "[00FF00]GREEN: 00FF00\n"
    "[0000FF]BLUE: 0000FF\n"
    "[FFFF00]YELLOW: FFFF00\n"
    "[FF00FF]PINK: FF00FF\n"
    "[00FFFF]CYAN: 00FFFF\n"
    "[FFA500]ORANGE: FFA500\n"
    "[FFFFFF]WHITE: FFFFFF"
)


async def cmd_color(chat_id, chat_type, key, iv):
    tp = _msg_type(chat_type)
    await reply_chat(COLOR_CODES_TEXT, chat_id, key, iv, tp=tp)


async def cmd_info(uid_str, chat_id, chat_type, key, iv):
    tp = _msg_type(chat_type)
    if not uid_str.isdigit():
        await reply_chat("[FFFFFF]Usage: /info <uid>", chat_id, key, iv, tp=tp)
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(INFO_API.format(uid_str))
            res = r.json()
            
        # Check if Name or Level exists directly in the response
        if not res or "Name" not in res:
            await reply_chat("[FF0000]No player found for this UID", chat_id, key, iv, tp=tp)
            return
            
        name = res.get("Name", "N/A")
        level = res.get("Level", "N/A")
        exp = res.get("Exp", 0)
        honor = res.get("Honor Score", "N/A")
        created = res.get("Created", "N/A")
        last_login = res.get("Last Login", "N/A")
        bio = res.get("Bio", "N/A")

        text = (
            "[00FF00]PLAYER INFO\n"
            f"[FFFFFF]Name: {name}\n"
            f"[FFFFFF]Level: {level}\n"
            f"[FFFFFF]Exp: {exp:,}\n"
            f"[FFFFFF]Honor Score: {honor}\n"
            f"[FFFFFF]Created: {created}\n"
            f"[FFFFFF]Last Login: {last_login}\n"
            "\n"
            "[00FF00]BIO\n"
            f"[FFFFFF]{bio}"
        )
    except Exception as e:
        print_error(f"/info error: {e}")
        text = "[FF0000]Player info check failed"
        
    await reply_chat(text, chat_id, key, iv, tp=tp)


async def cmd_check(uid_str, chat_id, chat_type, key, iv):
    tp = _msg_type(chat_type)
    if not uid_str.isdigit():
        await reply_chat("[FFFFFF]Usage: /check <uid>", chat_id, key, iv, tp=tp)
        return
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(BAN_CHECK_API.format(uid_str))
            data = r.json()
        status = data.get("status", "UNKNOWN")
    except Exception as e:
        print_error(f"/check error: {e}")
        status = "ERROR"
    color = "[FF0000]" if status.upper() == "BANNED" else "[00FF00]"
    await reply_chat(f"{color}{status}", chat_id, key, iv, tp=tp)


async def cmd_duo(uid_str, chat_id, chat_type, key, iv):
    """Same pattern as cmd_info / auto-msg — reply always goes through reply_chat."""
    tp = _msg_type(chat_type)
    if not uid_str.isdigit():
        await reply_chat("[FFFFFF]Usage: duo <uid>", chat_id, key, iv, tp=tp)
        return
    lines = ["[FF0000]Duo info check failed"]
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as c:
            r = await c.get(DUO_API.format(uid_str))
            print_debug(f"duo HTTP {r.status_code} for uid={uid_str}")
            if r.status_code != 200:
                lines = [f"[FF0000]Duo API error HTTP {r.status_code}"]
            else:
                res = r.json()
                if not res.get("success"):
                    msg = res.get("message") or "No duo info found for this UID"
                    lines = [f"[FF0000]{msg}"]
                else:
                    d = res.get("data", {}) or {}
                    # FF filter: 7+ digit ek box ma = hide. Partner UID half-half (max 4 digit/box)
                    def _uid_half(u):
                        s = "".join(c for c in str(u) if c.isdigit()) or str(u)
                        if not s:
                            return ["N/A"]
                        mid = (len(s) + 1) // 2  # first half slightly longer if odd
                        # hard cap 4 digit per box (safe under FF filter)
                        parts = []
                        for piece in (s[:mid], s[mid:]) if mid < len(s) else (s,):
                            for i in range(0, len(piece), 4):
                                parts.append(piece[i:i + 4])
                        return parts or [s]

                    lines = ["[FFFF00]DUO INFO"]
                    # Partner UID only — half / half (max 4 digit per box)
                    lines.append("[FFFF00]Partner UID:")
                    for chunk in _uid_half(d.get("partner_uid", "N/A")):
                        lines.append(f"[FFFF00]{chunk}")
                    # Aru details
                    lines.append(f"[FFFF00]Duo Level: {d.get('duo_level', 'N/A')}")
                    lines.append(f"[FFFF00]Intimacy Score: {d.get('intimacy_score', 'N/A')}")
                    lines.append(f"[FFFF00]Status: {d.get('status', 'N/A')}")
                    lines.append(f"[FFFF00]Days Active: {d.get('days_active', 'N/A')}")
                    lines.append(f"[FFFF00]Created On: {d.get('created_on', 'N/A')}")
    except Exception as e:
        print_error(f"duo error: {e}")
        lines = [f"[FF0000]Duo info check failed: {e}"]

    for i, line in enumerate(lines):
        print_debug(f"duo send[{i}] chat_id={chat_id} tp={tp} -> {line[:80]!r}")
        await reply_chat(line, chat_id, key, iv, tp=tp)
        await asyncio.sleep(0.4)
    print_success(f"duo done -> {chat_id} ({len(lines)} msgs)")


LOOP_EMOTES = {
    "AK47": 909000063, "MP40": 909000075, "FAMAS": 909000090,
    "UMP": 909000098, "GROZA": 909041005, "XM8": 909000085,
    "M4A1": 909033001, "M1887": 909035007, "MP5": 909033002,
}
LOOP_DELAY_SECONDS = 5  # /start le yati second ko antaral ma emote change garchha
_emote_loop_task = [None]  # /start le create garne, /stop le cancel garne


async def _emote_loop_worker(target_uid, key, iv, region, bot_uid):
    names = list(LOOP_EMOTES.items())
    i = 0
    try:
        while True:
            online_writer = _online_writer_ref[0]
            if online_writer is not None and not online_writer.is_closing():
                name, eid = names[i % len(names)]
                online_writer.write(build_emote_packet(bot_uid, target_uid, eid, key, iv, region))
                await online_writer.drain()
                i += 1
            await asyncio.sleep(LOOP_DELAY_SECONDS)
    except asyncio.CancelledError:
        raise


async def cmd_start_loop(target_uid, chat_id, chat_type, key, iv, region, bot_uid):
    tp = _msg_type(chat_type)
    if _emote_loop_task[0] is not None and not _emote_loop_task[0].done():
        await reply_chat("[FFFFFF]Loop is already running. Use /stop first.", chat_id, key, iv, tp=tp)
        return
    t = asyncio.create_task(_emote_loop_worker(target_uid, key, iv, region, bot_uid))
    _emote_loop_task[0] = t
    _ALL_TASKS.add(t)
    t.add_done_callback(_ALL_TASKS.discard)
    await reply_chat(f"[00FF00]Emote loop started ({LOOP_DELAY_SECONDS}s interval). Use /stop to end it.", chat_id, key, iv, tp=tp)


async def cmd_stop_loop(chat_id, chat_type, key, iv):
    tp = _msg_type(chat_type)
    t = _emote_loop_task[0]
    if t is None or t.done():
        await reply_chat("[FFFFFF]No loop is running.", chat_id, key, iv, tp=tp)
        return
    t.cancel()
    _emote_loop_task[0] = None
    await reply_chat("[00FF00]Emote loop stopped.", chat_id, key, iv, tp=tp)


REGION_CODE = {
    "VN": 1, "TH": 2, "ID": 3, "TW": 4, "BR": 5, "SG": 7,
    "US": 8, "RU": 11, "EUROPE": 12, "SAC": 19, "IND": 20,
    "ME": 21, "NA": 22, "PK": 23, "BD": 25,
}


def _squad_prefix(type_byte, region):
    """type_byte (e.g. 5) + region code (asal library ko region_map bata) = 4-hex-char header."""
    code = REGION_CODE.get((region or "").upper(), 21)  # default ME=21 (0x15)
    return f"{type_byte:02x}{code:02x}"


def build_open_squad_packet(size, key, iv, region, client_version):
    """asal library (Group_aenhaamdtsmodz.open_squad) bata verbatim liyeko -- team size = size-1."""
    fields = {
        1: 1,
        2: {
            2: bytes([11]), 3: 1, 4: int(size) - 1, 5: (region or "").lower(),
            8: {1: "IDC1", 2: 48, 3: region or ""}, 9: 2,
            10: bytes([1, 9, 10, 11, 18, 25, 32, 39]), 11: 1, 13: 1,
            14: {
                1: "080280006467A4C3020100000000000400050001000000004C3324180F0000004676251400000000000000000000000000000000000000ff00000000cacfa16d",
                2: 93,
                3: "p\\XT\u0013\u0002\tH\u0002\u0003\u0001\u000fS\u0005\u0002\u0002\u0004\u000f\u0004\u0002Q\b\u000f\u0002W\u0006\u0001VX\u0004TQ]P\u0003VR\u0001\u0007[\u0017\u0006\u0002EsXDEI\u001b\t\u0018\u0006\u001a\u001a\u0007\u0007H\u001aBcsXPC}w[RHepI\u001f^\\A\u0003WCBj_he\n\u0010\bJ\u001e\u0001^w\u001ck\u0005Ez\fUbCcr\u001cTQEFCAWg\u0001\u0001\u0007\u0004\u0017\u0006\u0000EgrAhei~[gQcDA}_\bDqC\u0003\u0004`cZmse\u000e\u001a\fK\\An\f{G\tq\u000bafX_^\u0002qX@ZSNF{Cc^G\f\u0013\u0001Ex[krOe\\}\u0006\u0000QCRZC\u0019lXA\u0005Avv\u0003\u0003p{\b\u0017\bLF\u000fSeIsUzq\u0001SwJY\u0000r~S{Na]|XVT~\u0004\u0015\u0004\fM\\\u0007q}}gLa\u0001{\u0006RXXsuVJZr\u0005BU\u0002n\u0004D\u000e\u001a\u0005Iw\u001e|UsL||qBYuittXMrbm^tuguv^\t\u0014\u0007E\u0007y\u001bg[cs{NDm~c\\W|~O\u001fD\u0001{\u0006l\u001bh_\u000e\u001a\u0006\u0007O\rd\u0005\fped~aDr}bvz\u007fC\u0003xBezC\u0002_]x\r\u0010\fHU\u0002vDVsuCZh`\u0000tTcLX\u0001dEtwBf\t\u0003p\t",
                4: "wY[Q", 6: 11, 7: {3: 2071688288}, 8: client_version, 9: 3, 10: 2,
                11: "\u0003bbSQ61JJBA4FAdV2KyoVlW39FjLPYC+QTWlQzE6kzmAk37hk/Va7/dNorNdc1eHg211Am98XSECZ0RYZxpWRRtGDQ/1nAcmsWIwu18IPWzwlEfZzZuQE47NiJwi198nygyf5T8NF0OL4csXLqyck5SHMRJrAZkxJs/c31i42BbSk21eOYArT1cYT6BUNKpWUA8687K8Za9Cnn89MydzMiKKC6ag7ozUK8XHdtpLB0cBNBkrojGLY2rTljHVpTUILrYM0mcPW3fWHT/+4c23m8owsCxfWtub2p0Oh9/PsXBy6Pp5RmZe5OM0mmAYaHb0cHPp924/gfUMH3X/pEGe0ykK3N5i7AvkOdIymfoV//W8Nah3fxtmCsK5mipYz4Vj3VRB4p+/vF/S0hxelTqwoQAzxicLPEYnEpCvccFTTdx/iSqkBn6AH+yUqlH8Y9aGSkiuu5SXHn7uIi1bFrANOgHNuy5tJjl024kovRsrLT5LlvimlmioUGMzYCSDncSmB3D5PnkhdBsmkNZyYJWYtOYS9TAgP8JSNUzL7AURIbnnj8XrWlPeYxN/oJqEjI2Tqjd5R7klw1YCoBsff/K9aMOsj8lFZtgvgAXhEbH4RFlQZ",
            },
            19: 329, 21: "7OR\u0019",
            24: [{1: 3, 2: 391}, {1: 4, 2: 385}, {1: 5, 2: 192}, {1: 29, 2: 204},
                 {1: 22, 2: 120}, {1: 14, 2: 175}, {1: 21}],
            27: "a_2504800200314510578",
        },
    }
    return _wrap_packet(_serialize(fields), _squad_prefix(5, region), key, iv)


def build_invite_squad_packet(slot, target_uid, key, iv, region):
    """asal library (Group_aenhaamdtsmodz.invite_squad) bata."""
    fields = {1: 2, 2: {1: int(target_uid), 2: region or "", 4: int(slot)}}
    return _wrap_packet(_serialize(fields), _squad_prefix(5, region), key, iv)


def build_emote_packet(bot_uid, target_uid, emote_id, key, iv, region):
    """asal library (Group_aenhaamdtsmodz.play_emote) bata -- field2:1 = bot ko aafnai UID, 2:2 = fixed 909000001."""
    fields = {1: 21, 2: {1: int(bot_uid), 2: 909000001, 5: {1: int(target_uid), 3: int(emote_id)}}}
    return _wrap_packet(_serialize(fields), _squad_prefix(5, region), key, iv)


def build_leave_squad_packet(bot_uid, key, iv, region):
    """asal library (Group_aenhaamdtsmodz.leave_squad) bata."""
    fields = {1: 7, 2: {1: int(bot_uid)}}
    return _wrap_packet(_serialize(fields), _squad_prefix(5, region), key, iv)


SQUAD_AUTO_LEAVE_SECONDS = 2  # /5, /6 pachi squad ma yati second baseko pachi bot aafai leave garchha


def get_whisper_map():
    """DEcwHisPErMsG_pb2 ko descriptor bata (Data field number, {uid, Chat_ID, chat_type, msg} numbers)."""
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    fdp = load_pb2_descriptor("DEcwHisPErMsG_pb2.py")
    msgs = {m.name: m for m in fdp.message_type}
    top = msgs["DecodeWhisper"]
    for n in top.nested_type:
        msgs.setdefault(n.name, n)
    data_field = next(f for f in top.field if f.name == "Data")
    inner = msgs[data_field.type_name.split(".")[-1]]
    _WHISPER = (data_field.number, {f.name: f.number for f in inner.field})
    return _WHISPER


_WHISPER = None


def parse_whisper(raw):
    data_num, nums = get_whisper_map()
    tree = Wlk(raw)
    if data_num + 1000 not in tree:
        return None
    inner = Wlk(bytes.fromhex(tree[data_num + 1000][0]))
    return {
        "uid": _pb_field(inner, nums["uid"]),
        "chat_id": _pb_field(inner, nums["Chat_ID"]),
        "chat_type": _pb_field(inner, nums["chat_type"]),
        "msg": _pb_field(inner, nums["msg"]),
    }


def _msg_type(chat_type):
    """chat_type: 1=clan, 2=private, kunai arko (squad, etc.) => None (squad-style packet)."""
    return chat_type if chat_type in (1, 2) else None


async def reply_chat(text, target_id, key, iv, tp=1):
    if chat_writer is None or chat_writer.is_closing():
        print_warning(f"reply_chat: chat socket not ready, dropped reply to {target_id}: {text[:40]!r}")
        return
    try:
        pkt = build_msg_packet(str(text), tp, int(target_id), key, iv)
        async with _get_chat_lock():
            if chat_writer is None or chat_writer.is_closing():
                print_warning(f"reply_chat: chat socket closed before write to {target_id}")
                return
            chat_writer.write(pkt)
            await chat_writer.drain()
        await asyncio.sleep(0.35)  # slow+safe gap so guild server le drop nagaros
        print_debug(f"reply_chat OK target={target_id} tp={tp} bytes={len(pkt)}")
    except Exception as e:
        print_error(f"reply_chat error: {e}")


async def cmd_open_squad(size, uid, chat_id, chat_type, key, iv, region, bot_uid, client_version):
    tp = _msg_type(chat_type)
    online_writer = _online_writer_ref[0]
    if online_writer is None or online_writer.is_closing():
        await reply_chat("[FFFFFF]Online connection not ready, try again shortly", chat_id, key, iv, tp=tp)
        return
    online_writer.write(build_open_squad_packet(size, key, iv, region, client_version))
    await online_writer.drain()
    await asyncio.sleep(0.3)
    online_writer.write(build_invite_squad_packet(1, uid, key, iv, region))
    await online_writer.drain()
    await asyncio.sleep(0.2)
    online_writer.write(build_invite_squad_packet(2, uid, key, iv, region))
    await online_writer.drain()
    t_em = asyncio.create_task(play_xm8_once(uid, key, iv, reason=f"squad{size}"))
    _ALL_TASKS.add(t_em)
    t_em.add_done_callback(_ALL_TASKS.discard)
    await reply_chat(f"[00FF00]Squad {size} opened, invite sent! "
                      f"Bot will leave automatically after {SQUAD_AUTO_LEAVE_SECONDS}s.", chat_id, key, iv, tp=tp)
    t = asyncio.create_task(_auto_leave_squad(bot_uid, key, iv, region))
    _ALL_TASKS.add(t)
    t.add_done_callback(_ALL_TASKS.discard)


async def _auto_leave_squad(bot_uid, key, iv, region):
    await asyncio.sleep(SQUAD_AUTO_LEAVE_SECONDS)
    online_writer = _online_writer_ref[0]
    if online_writer is not None and not online_writer.is_closing():
        online_writer.write(build_leave_squad_packet(bot_uid, key, iv, region))
        await online_writer.drain()
        print_info(f"Squad auto-left after {SQUAD_AUTO_LEAVE_SECONDS}s")


async def cmd_exit(chat_id, chat_type, key, iv, region, bot_uid):
    if _emote_loop_task[0] is not None and not _emote_loop_task[0].done():
        _emote_loop_task[0].cancel()
        _emote_loop_task[0] = None
    online_writer = _online_writer_ref[0]
    if online_writer is not None and not online_writer.is_closing():
        online_writer.write(build_leave_squad_packet(bot_uid, key, iv, region))
        await online_writer.drain()
    if chat_type == 2:  # private whisper channel matra close garne, guild chat haina
        await asyncio.sleep(0.2)
        if chat_writer is not None and not chat_writer.is_closing():
            chat_writer.write(build_leave_chat_packet(chat_id, key, iv))
            await chat_writer.drain()
    await reply_chat("[00FF00]Left the squad!", chat_id, key, iv, tp=_msg_type(chat_type))


def cmd_help():
    return ("[B][C][00FF00]\n"
            "[B][C][FFFFFF]FREE F[C][B][FFD700]I[B][C][FFFFFF]RE\n\n\n"
            "[B][C][00FF00]5 GROUP\n"
            "[B][C][FFFFFF]5\n"
            "[B][C][00FF00]6 GROUP\n"
            "[B][C][FFFFFF]6\n"
            "[B][C][00FF00]EMOTE (1-384 OR NAME)\n"
            "[B][C][FFFFFF]E <ID>\n"
            "[B][C][00FF00]EMOTE LOOP START\n"
            "[B][C][FFFFFF]START\n"
            "[B][C][00FF00]EMOTE LOOP STOP\n"
            "[B][C][FFFFFF]STOP\n"
            "[B][C][00FF00]LEAVE SQUAD\n"
            "[B][C][FFFFFF]EXIT\n"
            "[B][C][00FF00]BAN CHECK\n"
            "[B][C][FFFFFF]CHECK <UID>\n"
            "[B][C][00FF00]PLAYER INFO\n"
            "[B][C][FFFFFF]INFO <UID>\n"
            "[B][C][00FF00]DUO INFO\n"
            "[B][C][FFFFFF]DUO <UID>\n"
            "[B][C][00FF00]COLOR CODES\n"
            "[B][C][FFFFFF]COLOR\n\n\n"
            "[B][C][00FF00]DEV : [B][C][FFFFFF]CKRPRO\n"
            "[B][C][00FF00]TIKTOK : [B][C][FFFFFF]CKRPRO")


async def cmd_emote(arg, uid, chat_id, chat_type, key, iv, region, bot_uid):
    tp = _msg_type(chat_type)
    eid = _find_emote(arg)
    if not eid:
        await reply_chat("[FFFFFF]Invalid. /e <1-384> or a name. Example: /e 17 or /e ak", chat_id, key, iv, tp=tp)
        return
    online_writer = _online_writer_ref[0]
    if online_writer is None or online_writer.is_closing():
        await reply_chat("[FFFFFF]Online connection not ready", chat_id, key, iv, tp=tp)
        return
    online_writer.write(build_emote_packet(bot_uid, uid, eid, key, iv, region))
    await online_writer.drain()
    await reply_chat(f"[00FF00]EMOTE --> {arg}", chat_id, key, iv, tp=tp)


async def _notify_processing(chat_id, tp, key, iv):
    await reply_chat("[FFFF00]Processing...", chat_id, key, iv, tp=tp)


KNOWN_COMMANDS = {"5", "6", "e", "exit", "help", "start", "stop", "check", "info", "duo", "color"}


async def dispatch_command(raw_msg, uid, chat_id, chat_type, key, iv, region, bot_uid, client_version):
    text = re.sub(r"^(\[[0-9A-Fa-f]{6}\])+", "", str(raw_msg).strip()).strip()
    if not text:
        return
    had_slash = text.startswith("/")
    body = text[1:] if had_slash else text
    cmd, _, arg = body.partition(" ")
    cmd, arg = cmd.lower(), arg.strip()

    if cmd not in KNOWN_COMMANDS:
        # Naya "/" nabhayeko chat text lai command jasto naliyera chup basne -- natra
        # normal kura garda pani bot le "Unknown command" bhandai spam garchha.
        if had_slash:
            tp = _msg_type(chat_type)
            await reply_chat("[FFFFFF]Unknown command. Try help", chat_id, key, iv, tp=tp)
        return

    target = int(arg) if arg.isdigit() else uid
    tp = _msg_type(chat_type)
    try:
        await _run_command(cmd, arg, target, uid, chat_id, chat_type, key, iv, region, bot_uid, client_version, tp)
    except Exception as e:
        import traceback
        print_error(f"/{cmd} crashed: {e}")
        traceback.print_exc()
        await reply_chat(f"[FF0000]/{cmd} failed: {e}", chat_id, key, iv, tp=tp)


async def _run_command(cmd, arg, target, uid, chat_id, chat_type, key, iv, region, bot_uid, client_version, tp):
    if cmd == "5":
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_open_squad(5, target, chat_id, chat_type, key, iv, region, bot_uid, client_version)
    elif cmd == "6":
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_open_squad(6, target, chat_id, chat_type, key, iv, region, bot_uid, client_version)
    elif cmd == "e":
        if not arg:
            await reply_chat("[FFFFFF]Usage: /e <1-384> or a name", chat_id, key, iv, tp=tp)
            return
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_emote(arg, uid, chat_id, chat_type, key, iv, region, bot_uid)
    elif cmd == "start":
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_start_loop(uid, chat_id, chat_type, key, iv, region, bot_uid)
    elif cmd == "stop":
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_stop_loop(chat_id, chat_type, key, iv)
    elif cmd == "check":
        if not arg:
            await reply_chat("[FFFFFF]Usage: /check <uid>", chat_id, key, iv, tp=tp)
            return
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_check(arg, chat_id, chat_type, key, iv)
    elif cmd == "color":
        await cmd_color(chat_id, chat_type, key, iv)
    elif cmd == "info":
        if not arg:
            await reply_chat("[FFFFFF]Usage: /info <uid>", chat_id, key, iv, tp=tp)
            return
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_info(arg, chat_id, chat_type, key, iv)
    elif cmd == "duo":
        if not arg:
            await reply_chat("[FFFFFF]Usage: duo <uid>", chat_id, key, iv, tp=tp)
            return
        # Processing skip — duo API dherai chito, double-msg server le drop garna sakcha
        await cmd_duo(arg, chat_id, chat_type, key, iv)
    elif cmd == "exit":
        await _notify_processing(chat_id, tp, key, iv)
        await cmd_exit(chat_id, chat_type, key, iv, region, bot_uid)
    elif cmd == "help":
        await reply_chat(cmd_help(), chat_id, key, iv, tp=tp)
    else:
        await reply_chat("[FFFFFF]Unknown command. Try /help", chat_id, key, iv, tp=tp)


async def handle_chat_packet(data, key, iv, region, bot_uid, client_version):
    try:
        m = None
        for off in (5, 0, 1, 3, 7, 9):
            if off >= len(data):
                continue
            try:
                m = parse_whisper(data[off:])
            except Exception:
                m = None
            if m and m.get("msg"):
                break
        if not m or not m.get("msg"):
            return
        if bot_uid and m.get("uid") == bot_uid:
            return
        print_debug(f"chat from {m.get('uid')} type={m.get('chat_type')} id={m.get('chat_id')}: {str(m.get('msg'))[:80]}")
        await dispatch_command(m["msg"], m["uid"], m["chat_id"], m["chat_type"], key, iv, region, bot_uid, client_version)
    except Exception as e:
        print_error(f"chat packet error: {e}")


_online_writer_ref = [None]  # tcp_connect() ma set huncha, emote/invite online socket bata jaanchha
_bot_identity = {"uid": None, "name": None, "region": None}  # run_account() ma set huncha


async def tcp_chat_connect(ip, port, auth_packet, key, iv, clan_id, clan_data, region=None, bot_uid=None, client_version=None):
    global chat_writer
    while not shutdown_requested:
        writer = None
        try:
            reader, writer = await asyncio.open_connection(ip, int(port))
            writer.write(bytes.fromhex(auth_packet))
            await writer.drain()
            if clan_id:
                writer.write(build_clan_auth_packet(clan_id, clan_data, key, iv))
                await writer.drain()
            chat_writer = writer
            print_success("Chat TCP online")

            while not shutdown_requested:
                data = await reader.read(9999)
                if not data:
                    print_warning("Chat server closed the connection")
                    break
                hx = data.hex()
                if hx.startswith("12"):
                    t = asyncio.create_task(handle_chat_packet(data, key, iv, region, bot_uid, client_version))
                    _ALL_TASKS.add(t)
                    t.add_done_callback(_ALL_TASKS.discard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print_error(f"Chat TCP err: {e}")
        finally:
            chat_writer = None
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
        await asyncio.sleep(2)


# ══════════════════════════════════════════════════════════════════════════════
#  INVITE TCP (online port)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  SQUAD WELCOME MESSAGE (invite accept gare pachi squad chat ma message)
# ══════════════════════════════════════════════════════════════════════════════
WELCOME_ENABLED = True       # False garepachi welcome message pathaudaina
WELCOME_FILE = "welcome.txt"  # puro file ek message ho (multi-line milchha)
DEFAULT_WELCOME = "[00FF00]WELCOME!"

# Invite accept / squad invite pachi ek choti XM8 emote
AUTO_XM8_EMOTE = True
XM8_EMOTE_ID = 909000085
XM8_EMOTE_DELAY = 0.8


def _find_file(name):
    here = os.path.dirname(os.path.abspath(__file__))
    for fp in (os.path.join(here, name), os.path.abspath(name)):
        if os.path.isfile(fp):
            return fp
    return None


def load_welcome():
    fp = _find_file(WELCOME_FILE)
    if fp:
        try:
            with open(fp, "r", encoding="utf-8-sig") as f:
                text = f.read().strip()
            if text:
                return text
        except Exception as e:
            print_error(f"{WELCOME_FILE} read error: {e}")
    return DEFAULT_WELCOME


GHOST_ENABLED = False   # True = invite aayepachi accept nagari, ghost packet pathaune
GHOST_NAME = "Ghost"    # ghost ko naam ([C][B][RRGGBB] color code milchha)


def build_exit_packet(key, iv):
    return _wrap_packet(_serialize({1: 7, 2: {1: 0}}), "0515", key, iv)


def build_ghost_packet(owner_uid, name, squad_code, key, iv):
    fields = {1: 61, 2: {
        1: int(owner_uid),
        2: {1: int(owner_uid), 2: 1159, 3: name, 5: 12, 6: 9999999, 7: 1, 8: {2: 1, 3: 1}, 9: 3},
        3: squad_code,
    }}
    return _wrap_packet(_serialize(fields), "0515", key, iv)


def build_leave_chat_packet(owner_uid, key, iv):
    fields = {1: 4, 2: {1: int(owner_uid), 3: "en"}}
    return _wrap_packet(_serialize(fields), "1215", key, iv)


def build_join_chat_packet(owner_uid, chat_code, key, iv):
    fields = {1: 3, 2: {1: int(owner_uid), 3: "en", 4: str(chat_code)}}
    return _wrap_packet(_serialize(fields), "1215", key, iv)


async def play_xm8_once(target_uid, key, iv, reason="invite"):
    """Invite accept / squad invite pachi single XM8 emote (ek choti)."""
    if not AUTO_XM8_EMOTE:
        return
    try:
        await asyncio.sleep(XM8_EMOTE_DELAY)
        online_writer = _online_writer_ref[0]
        bot_uid = _bot_identity.get("uid")
        region = _bot_identity.get("region") or "SG"
        if not bot_uid or online_writer is None or online_writer.is_closing():
            print_warning("XM8 emote skipped: socket/uid not ready")
            return
        pkt = build_emote_packet(int(bot_uid), int(target_uid), int(XM8_EMOTE_ID), key, iv, region)
        online_writer.write(pkt)
        await online_writer.drain()
        print_success(f"XM8 emote -> {target_uid} ({reason})")
    except Exception as e:
        print_error(f"play_xm8_once error: {e}")


async def send_squad_welcome(owner_uid, chat_code, key, iv):
    try:
        for _ in range(10):  # chat connection ready hunu parkhine (max 10s)
            if chat_writer is not None and not chat_writer.is_closing():
                break
            await asyncio.sleep(1)
        else:
            print_warning("Welcome skipped: chat connection ready chhaina")
            return
        chat_writer.write(build_join_chat_packet(owner_uid, chat_code, key, iv))
        await chat_writer.drain()
        await asyncio.sleep(0.5)
        chat_writer.write(build_msg_packet(load_welcome(), None, owner_uid, key, iv))
        await chat_writer.drain()
        print_success(f"Welcome sent => squad of {owner_uid}")
        await asyncio.sleep(1)
        if chat_writer is not None and not chat_writer.is_closing():
            chat_writer.write(build_leave_chat_packet(owner_uid, key, iv))
            await chat_writer.drain()
    except Exception as e:
        print_error(f"send_squad_welcome error: {e}")


async def tcp_incoming_handler(reader, writer, key, iv, game_version="1.132.1"):
    global connection_closed

    try:
        while not connection_closed and not shutdown_requested:
            try:
                data = await asyncio.wait_for(reader.read(65535), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not data:
                print_warning("TCP dropped by server")
                connection_closed = True
                break

            hex_data = data.hex()

            if hex_data.startswith("0500") and len(hex_data) > 40:
                try:
                    decoded_json = await decode_protobuf(hex_data[10:])
                    decoded = _flatten_node(json.loads(decoded_json))

                    try:
                        inv = decoded["5"]
                        owner_uid = inv["1"]
                        chat_code = inv.get("17")
                        handled = False

                        if GHOST_ENABLED:
                            squad_code = inv.get("31")
                            if owner_uid and squad_code:
                                writer.write(build_exit_packet(key, iv))
                                await writer.drain()
                                await asyncio.sleep(0.05)
                                writer.write(build_ghost_packet(owner_uid, GHOST_NAME, squad_code, key, iv))
                                await writer.drain()
                                print(f"Ghost sent => {owner_uid}")
                                handled = True
                            else:
                                print_warning("Ghost: invite ma squad code (field 31) bhetiyena")

                        if not handled:
                            code_str = str(inv["8"])
                            if not owner_uid or len(code_str) < 10 or not code_str.replace("_", "").isalnum():
                                continue
                            ok = await send_accept_invite_packet(owner_uid, code_str, key, iv, writer, game_version)
                            if ok:
                                print(f"Invite Accepted From => {owner_uid}")
                                handled = True
                                t_em = asyncio.create_task(play_xm8_once(owner_uid, key, iv, reason="accept"))
                                _ALL_TASKS.add(t_em)
                                t_em.add_done_callback(_ALL_TASKS.discard)

                        if handled and WELCOME_ENABLED and chat_code:
                            t = asyncio.create_task(send_squad_welcome(owner_uid, chat_code, key, iv))
                            _ALL_TASKS.add(t)
                            t.add_done_callback(_ALL_TASKS.discard)
                    except (KeyError, TypeError, AttributeError):
                        pass
                except Exception:
                    pass

    except asyncio.CancelledError:
        raise
    except Exception as e:
        print_error(f"tcp_incoming_handler error: {e}")
        connection_closed = True


async def tcp_connect(ip, port, starter_packet, key, iv, game_version, nickname):
    global connection_closed

    while not shutdown_requested:
        writer = None
        reader = None
        listener_task = None
        try:
            reader, writer = await asyncio.open_connection(ip, int(port))
            writer.write(bytes.fromhex(starter_packet))
            await writer.drain()

            print(f"Tcp Online => {nickname}")
            connection_closed = False

            _online_writer_ref[0] = writer
            listener_task = asyncio.create_task(
                tcp_incoming_handler(reader, writer, key, iv, game_version)
            )
            _ALL_TASKS.add(listener_task)
            listener_task.add_done_callback(_ALL_TASKS.discard)

            while not connection_closed and not shutdown_requested:
                await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print_error(f"TCP Err: {e}")

        finally:
            connection_closed = True
            _online_writer_ref[0] = None
            if listener_task:
                listener_task.cancel()
                try:
                    await listener_task
                except Exception:
                    pass
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        if shutdown_requested:
            break

        print_warning("TCP dropped — reconnecting in 2s...")
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise


# ══════════════════════════════════════════════════════════════════════════════
#  ONLINE AUTH PACKET  ← FIXED
# ══════════════════════════════════════════════════════════════════════════════
async def create_auth_token_online(account_id, jwt_token, timestamp, key, iv):
    """
    Correct online auth packet:
        830c | uid(8 BE) | kts(4 BE) | enc_len(8 BE) | AES-CBC(token)
    Returns hex string (tcp_connect does bytes.fromhex()).
    """
    try:
        if isinstance(key, str): K = bytes.fromhex(key)
        else:                   K = bytes(key)
        if isinstance(iv, str): V = bytes.fromhex(iv)
        else:                   V = bytes(iv)

        jwt_bytes = jwt_token.encode("utf-8") if isinstance(jwt_token, str) else bytes(jwt_token)
        encrypted = AES.new(K, AES.MODE_CBC, V).encrypt(pad(jwt_bytes, AES.block_size))

        packet = (
            bytes.fromhex("830c")
            + int(account_id).to_bytes(8, "big")
            + int(timestamp).to_bytes(4, "big")
            + len(encrypted).to_bytes(8, "big")
            + encrypted
        )
        print_debug(
            f"online auth: opcode=830c uid={account_id} "
            f"kts={timestamp} enc={len(encrypted)}B"
        )
        return packet.hex()
    except Exception as e:
        print_error(f"[AUTH TOKEN ERROR] {e}")
        return None


async def get_playstore_version():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: play_scraper('com.dts.freefireth', lang='hi', country='id')
    )
    return result.get("version")


async def version_config():
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        app_version = await get_playstore_version()
        api_url = (
            "https://version.ggwhitehawk.com/live/ver.php"
            f"?version={app_version}"
            "&lang=hi&device=android&channel=android"
            "&appstore=googleplay&region=IND"
            "&whitelist_version=1.3.0&whitelist_sp_version=1.0.0"
        )
        try:
            response = await c.get(api_url)
            response.raise_for_status()
            data = response.json()
            server_url = data.get("server_url")
            remote_version = data.get("remote_version")
            latest_release_version = data.get("latest_release_version")
            if not server_url or not remote_version or not latest_release_version:
                return None
            return latest_release_version, remote_version, server_url
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  MAJORLOGIN  ← FIXED (manual protobuf, no Online_pb2)
# ══════════════════════════════════════════════════════════════════════════════
async def build_majorlogin_payload(open_id, access_token, platform, client_version):
    try:
        fields = {
            3:  str(datetime.now())[:-7],                # event_time
            4:  "free fire",                             # game_name
            5:  2,                                       # platform_id
            7:  client_version,                          # client_version
            8:  "Android OS 11 / API-30",                # system_software
            9:  "Handheld",                              # system_hardware
            10: "Verizon",                               # telecom_operator
            11: "WIFI",                                  # network_type
            12: 1080,                                    # screen_width
            13: 2400,                                    # screen_height
            14: "440",                                   # screen_dpi
            15: "ARMv8",                                 # processor_details
            16: 6144,                                    # memory
            17: "Adreno (TM) 650",                       # gpu_renderer
            18: "OpenGL ES 3.2",                         # gpu_version
            19: "Google|34a7dcdf-a7d5-4cb6-8d7e-3b0e448a0c57",
            20: "",                                      # client_ip
            21: "en",                                    # language
            22: open_id,                                 # open_id
            23: str(platform),                           # open_id_type
            24: "Handheld",                              # device_type
            25: "realme RMX2189",                        # device_model
            26: "IND",                                   # region
            29: access_token,                            # access_token
            30: 3,                                       # login_by
            41: "Verizon",                               # network_operator_a
            42: "WIFI",                                  # network_type_a
            57: "7428b253defc164018c604a1ebbfebdf",      # client_using_version (long)
            60: 128512,                                  # external_storage_total
            61: 45000,                                   # external_storage_available
            62: 110731,                                  # internal_storage_total
            64: 25000,                                   # internal_storage_available
            65: 26628,                                   # game_disk_storage_total
            66: 20000,                                   # game_disk_storage_available
            67: 119234,                                  # external_sdcard_total
            73: 1,
            74: "/data/app/~~random/base.apk",           # library_path
            76: 2,                                       # platform_sdk_id
            77: "hash|base.apk",                         # library_token
            78: 2,                                       # login_open_id_type
            79: 2,                                       # origin_platform_type
            81: "64",                                    # cpu_architecture
            86: "OpenGLES3",                             # graphics_api
            87: 16383,                                   # supported_astc_bitset
            88: 3,                                       # channel_type
            92: 12000,                                   # loading_time
            93: "android",                               # release_channel
            94: bytes.fromhex("1704154E050F5F551A5259430F071609106958050E520B2F035B275A505A3B590562"),
            95: 1,                                       # reg_avatar
            97: 1,                                       # if_push
            98: 0,                                       # is_vpn
            99: str(platform),                           # primary_platform_type
            100: str(platform),
            102: "2024010012",                           # client_version_code
            104: 110009,                                 # android_engine_init_flag
            105: 1,
            106: "https://dl-bs.ggpolarbear.com/live/ABHotUpdates/|https://core-bs.ggpolarbear.com/live/ABHotUpdates/|a4332cb1c1a84e51dd77441e4856ed5a",
            107: "1.9393e7b8a53e8aeb",
        }
        payload = _serialize(fields)
        return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(payload, AES.block_size))
    except Exception as e:
        print_error(f"build_majorlogin_payload error: {e}")
        return None


def RdVar(B, Pos, End):
    V = 0
    Sh = 0
    while Pos < End:
        X = B[Pos]
        Pos += 1
        V |= (X & 0x7F) << Sh
        if not X & 0x80:
            break
        Sh += 7
    return V, Pos


def Wlk(B, Pos=0, End=None):
    if End is None:
        End = len(B)
    Out = {}
    while Pos < End:
        Tag, Pos = RdVar(B, Pos, End)
        F = Tag >> 3
        W = Tag & 7
        if W == 0:
            V, Pos = RdVar(B, Pos, End)
            Out.setdefault(F, []).append(V)
        elif W == 2:
            Ln, Pos = RdVar(B, Pos, End)
            if Pos + Ln > End:
                break
            Raw = B[Pos:Pos + Ln]
            Pos += Ln
            Out.setdefault(F + 1000, []).append(Raw.hex())
            DecOk = False
            try:
                S = Raw.decode("utf-8")
                if all(C.isprintable() or C in "\n\r\t" for C in S):
                    Out.setdefault(F, []).append(S)
                    DecOk = True
            except Exception:
                pass
            if not DecOk:
                Sub = None
                try:
                    Sub = Wlk(Raw, 0, len(Raw))
                    if not Sub:
                        Sub = None
                except Exception:
                    Sub = None
                Out.setdefault(F, []).append(Sub if Sub else Raw.hex())
        elif W == 5:
            V = struct.unpack_from("<f", B, Pos)[0]
            Pos += 4
            Out.setdefault(F, []).append(V)
        elif W == 1:
            V = struct.unpack_from("<d", B, Pos)[0]
            Pos += 8
            Out.setdefault(F, []).append(V)
        else:
            break
    return Out


def FndProtoRoot(Raw):
    for I in range(len(Raw) - 10):
        if Raw[I] in (0x08, 0x12):
            try:
                T = Wlk(Raw, I, len(Raw))
                if 1 in T and isinstance(T[1][0], int) and T[1][0] > 100000000:
                    return I, T
                if 2 in T and isinstance(T[2][0], str) and T[2][0].isalpha():
                    return I, T
            except Exception:
                continue
    return 0, {}


def SrchJwtInVals(Vals):
    if not isinstance(Vals, list):
        Vals = [Vals]
    for V in Vals:
        if isinstance(V, str):
            if V.startswith("eyJ") and V.count(".") == 2 and len(V) > 100:
                return V
            if len(V) > 20 and all(C in "0123456789abcdefABCDEF" for C in V):
                try:
                    Dec = bytes.fromhex(V)
                    Idx = Dec.find(b"eyJ")
                    if Idx != -1:
                        Allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
                        End = Idx
                        while End < len(Dec) and Dec[End] in Allowed:
                            End += 1
                        Cand = Dec[Idx:End].decode("ascii", errors="ignore")
                        if Cand.count(".") == 2 and len(Cand) > 100:
                            return Cand
                except Exception:
                    pass
        elif isinstance(V, dict):
            Found = ExtractJwtFromTree(V)
            if Found:
                return Found
        elif isinstance(V, list):
            Found = SrchJwtInVals(V)
            if Found:
                return Found
    return None


def ExtractJwtFromTree(Tree):
    if not isinstance(Tree, dict):
        return None
    V8 = Tree.get(8)
    if V8:
        Found = SrchJwtInVals(V8)
        if Found:
            return Found
    for Fnum, Vals in Tree.items():
        Found = SrchJwtInVals(Vals)
        if Found:
            return Found
    return None


def ExtractJwt(Raw):
    if not Raw:
        return None
    Off, Tree = FndProtoRoot(Raw)
    Jwt = ExtractJwtFromTree(Tree)
    if Jwt:
        return Jwt
    Marker = b"eyJ"
    Start = Raw.find(Marker)
    while Start != -1:
        Allowed = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
        End = Start
        while End < len(Raw) and Raw[End] in Allowed:
            End += 1
        Cand = Raw[Start:End].decode("utf-8", errors="ignore")
        if Cand.count(".") == 2 and len(Cand) > 100:
            return Cand
        Start = Raw.find(Marker, Start + 1)
    return None


def TreeFlat(Tree):
    Out = {}
    for K, V in Tree.items():
        if isinstance(V, list) and len(V) == 1:
            Out[str(K)] = V[0]
        else:
            Out[str(K)] = V
    return Out


async def send_majorlogin(data, release_version, server_url):
    Hdr = {
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*",
        "Accept-Encoding": "deflate, gzip",
        "X-Ga-Sv": str(int(time.time())),
        "Authorization": "Bearer",
        "X-Ga": "v1 1",
        "Releaseversion": release_version,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.12f1",
    }
    async with httpx.AsyncClient(verify=False, timeout=15.0) as c:
        for Attempt in range(3):
            try:
                R = await c.post(server_url.rstrip("/") + "/MajorLogin", headers=Hdr, content=data)
                if R.status_code == 200:
                    return R
            except Exception:
                pass
            if Attempt < 2:
                await asyncio.sleep(1)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  GETLOGINDATA  ← FIXED (no Online_pb2, Wlk parse only)
# ══════════════════════════════════════════════════════════════════════════════
async def send_getlogin(data, base_url, token, release_version):
    hdrs = {
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*",
        "Accept-Encoding": "deflate, gzip",
        "X-GA-SV": str(int(time.time())),
        "Authorization": f"Bearer {token}",
        "X-GA": "v1 1",
        "ReleaseVersion": release_version,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.12f1",
    }
    url = f"{base_url.rstrip('/')}/GetLoginData"
    async with httpx.AsyncClient(verify=False, timeout=10.0) as c:
        try:
            r = await c.post(url, headers=hdrs, data=data)
            if r.status_code != 200:
                print_warning(f"GetLoginData HTTP {r.status_code}")
                return None
            parsed = Wlk(r.content)
            return parsed, r.content
        except Exception as e:
            print_error(f"send_getlogin: {e}")
            return None


# ══════════════════════════════════════════════════════════════════════════════
#  ACCOUNT DICT  ← FIXED (reads from both trees)
# ══════════════════════════════════════════════════════════════════════════════
def _tree_first(tree, key, default=None):
    """tree can be Wlk() (int keys) or TreeFlat() (str keys)."""
    if not isinstance(tree, dict):
        return default
    v = tree.get(key)
    if v is None:
        v = tree.get(str(key))
    if isinstance(v, list):
        v = v[0] if v else None
    return v if v is not None else default


def _as_str(v, default=""):
    if v is None: return default
    if isinstance(v, (bytes, bytearray)):
        try: return v.decode("utf-8", "ignore")
        except Exception: return default
    return str(v)


def _build_account_dict(flat, token, login_tree, release_version, client_version,
                        server_url, platform, aes_ak, iv_i, extra, region="IND"):
    """flat     = TreeFlat(MajorLogin)  → string keys
       login_tree = Wlk(GetLoginData)  → int keys"""
    if login_tree is None:
        login_tree = {}

    online = _tree_first(flat, "14") or _tree_first(login_tree, 14)
    if isinstance(online, (bytes, bytearray)):
        online = online.decode("utf-8", "ignore")

    account_id = _tree_first(flat, "1") or _tree_first(login_tree, 1)
    srv_time   = _tree_first(flat, "21") or _tree_first(login_tree, 21) or int(time.time())
    nickname   = (_as_str(_tree_first(flat, "4")) or _as_str(_tree_first(login_tree, 4)) or "Bot")
    reg = region
    for _c in (_tree_first(flat, "2"), _tree_first(flat, "3"),
               _tree_first(login_tree, 3), _tree_first(login_tree, 2)):
        if _c is not None and _as_str(_c).upper() in REGION_CODE:
            reg = _c
            break

    return {
        'account_id':          account_id,
        'nickname':            nickname,
        'region':              str(reg).upper(),
        'token':               token,
        'server_time':         int(srv_time),
        'aes_ak':              aes_ak,
        'iv_i':                iv_i,
        'functional_addrs':    online,
        'informational_addrs': None,
        'clan_id':             extra.get('clan_id'),
        'clan_data':           extra.get('clan_data'),
        'chat_addr':           extra.get('chat_addr'),
        'release_version':     release_version,
        'client_version':      client_version,
        'server_url':          server_url,
        'platform':            platform,
    }


async def _finish_login(login_payload_data, release_version, client_version, server_url, platform):
    majorlogin_rsp = await send_majorlogin(login_payload_data, release_version, server_url)
    if majorlogin_rsp is None:
        return None

    raw = majorlogin_rsp.content
    off, tree = FndProtoRoot(raw)
    flat = TreeFlat(tree)
    token = ExtractJwt(raw)

    if not token:
        print_error("No JWT in MajorLogin response")
        return None

    account_id = flat.get("1")
    aes_ak = flat.get("1022") or flat.get("22")
    iv_i = flat.get("1023") or flat.get("23")
    url_addr = flat.get("10") or server_url

    if isinstance(aes_ak, str):
        try:
            aes_ak = bytes.fromhex(aes_ak)
        except Exception:
            pass
    if isinstance(iv_i, str):
        try:
            iv_i = bytes.fromhex(iv_i)
        except Exception:
            pass

    if not account_id:
        print_error("account_id missing")
        return None
    if not isinstance(aes_ak, (bytes, bytearray)) or len(aes_ak) != 16:
        print_error(f"aes_ak invalid: {aes_ak!r}")
        return None
    if not isinstance(iv_i, (bytes, bytearray)) or len(iv_i) != 16:
        print_error(f"iv_i invalid: {iv_i!r}")
        return None

    res = await send_getlogin(login_payload_data, url_addr, token, release_version)
    if res is None:
        print_error("GetLoginData failed")
        return None
    login_tree, login_raw = res

    # Extract clan + chat from GetLoginData
        # Extract clan + chat from GetLoginData (descriptor bata asal field number)
    extra = {}
    try:
        nums = get_login_field_numbers()
        print_debug(f"login fields: {nums}")
        tree = Wlk(login_raw)
        extra = {
            'clan_id': _pb_field(tree, nums["Clan_ID"]) or None,
            'clan_data': _pb_field(tree, nums["Clan_Compiled_Data"]),
            'chat_addr': _pb_field(tree, nums["AccountIP_Port"]),
        }
    except Exception as e:
        print_error(f"login data parse error: {e}")
        extra = {}
    if isinstance(extra.get('chat_addr'), (bytes, bytearray)):
        extra['chat_addr'] = extra['chat_addr'].decode("utf-8", "ignore")
    if extra.get('clan_id') is not None:
        try:
            extra['clan_id'] = int(extra['clan_id'])
        except Exception:
            pass
    print_debug(f"login fields  clan_id={extra.get('clan_id')}  chat={extra.get('chat_addr')}")

    return _build_account_dict(flat, token, login_tree, release_version,
                               client_version, server_url, platform,
                               aes_ak, iv_i, extra)


async def process_account_uid_pass(uid: str, password: str) -> Optional[Dict]:
    try:
        verconfig_res = await version_config()
        if verconfig_res is None:
            return None
        release_version, client_version, server_url = verconfig_res

        import requests
        url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
        hdrs = {
            "User-Agent": "GarenaMSDK/5.5.2P3(RMX3085;Android 15;en-US;IND;)",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
        }
        data = {
            "uid": uid,
            "password": password,
            "response_type": "token",
            "client_type": "2",
            "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
            "client_id": "100067"
        }
        resp = await asyncio.to_thread(requests.post, url, headers=hdrs, data=data, timeout=10)
        tok = resp.json()

        open_id = tok.get("open_id")
        access_token = tok.get("access_token")
        platform = tok.get("platform", 4)

        if not open_id or not access_token:
            return None

        login_payload_data = await build_majorlogin_payload(open_id, access_token, str(platform), client_version)
        if not login_payload_data:
            return None

        return await _finish_login(login_payload_data, release_version, client_version, server_url, platform)
    except Exception as e:
        print_error(f"process_account_uid_pass error: {e}")
        return None


async def process_account_token(access_token: str) -> Optional[Dict]:
    try:
        verconfig_res = await version_config()
        if verconfig_res is None:
            return None
        release_version, client_version, server_url = verconfig_res

        import requests
        url = f"https://100067.connect.garena.com/oauth/token/inspect?token={access_token}"
        hdrs = {
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "close",
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "100067.connect.garena.com",
            "User-Agent": "GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)"
        }
        resp = await asyncio.to_thread(requests.get, url, headers=hdrs, timeout=10)
        data = resp.json()

        if 'error' in data:
            return None

        open_id = data.get('open_id')
        platform = data.get('platform', 4)

        if not open_id:
            return None

        login_payload_data = await build_majorlogin_payload(open_id, access_token, str(platform), client_version)
        if not login_payload_data:
            return None

        return await _finish_login(login_payload_data, release_version, client_version, server_url, platform)
    except Exception as e:
        print_error(f"process_account_token error: {e}")
        return None


def _addr(a):
    a = a.decode() if isinstance(a, bytes) else a
    ip, port = a.split(":")
    return ip, port


async def run_account(account_data: Dict):
    tcp_packet = await create_auth_token_online(
        account_data['account_id'],
        account_data['token'],
        account_data['server_time'],
        account_data['aes_ak'],
        account_data['iv_i']
    )
    if tcp_packet is None:
        print_error("Failed to build auth token")
        return

    key, iv = account_data['aes_ak'], account_data['iv_i']
    ip, port = _addr(account_data['functional_addrs'])

    _bot_identity["uid"] = int(account_data['account_id'])
    _bot_identity["name"] = account_data.get('nickname') or "Bot"
    _bot_identity["region"] = account_data.get('region') or "SG"

    tasks = [
        tcp_connect(ip, port, tcp_packet, key, iv,
                    account_data['client_version'], account_data['nickname'])
    ]

    clan_id = account_data.get('clan_id')
    chat_addr = account_data.get('chat_addr')
    print_debug(f"clan_id={clan_id} chat_addr={chat_addr}")
    if chat_addr:
        cip, cport = _addr(chat_addr)
        chat_auth = build_auth_0115(account_data['account_id'], account_data['token'],
                                    account_data['server_time'], key, iv)
        tasks.append(tcp_chat_connect(cip, cport, chat_auth, key, iv,
                                      clan_id, account_data.get('clan_data'),
                                      account_data.get('region'),
                                      int(account_data['account_id']),
                                      account_data.get('client_version')))
        if AUTO_MESSAGE and clan_id:
            tasks.append(auto_message_scheduler(key, iv, clan_id))
        else:
            print_warning("Auto-msg skipped (disabled or no clan_id)")
    else:
        print_warning("Chat server address aayena — commands/auto-msg chalenan")

    await asyncio.gather(*tasks)


async def run_guest_account(uid: str, password: str):
    account_data = await process_account_uid_pass(uid, password)
    if not account_data:
        print_error("Login failed")
        return
    await run_account(account_data)


async def run_token_account(access_token: str):
    account_data = await process_account_token(access_token)
    if not account_data:
        print_error("Login failed")
        return
    await run_account(account_data)


def print_menu():
    print()
    print("Select an option:")
    print("  1. UiD Pw")
    print("  2. Access Token")
    print("  3. Exit")
    print()


CREDENTIALS_FILE = "ckr.txt"


def _cred_paths():
    here = os.path.dirname(os.path.abspath(__file__))
    paths = [os.path.join(here, CREDENTIALS_FILE), os.path.abspath(CREDENTIALS_FILE)]
    return list(dict.fromkeys(paths))


def read_credentials(path=None):
    """ckr.txt bata uid/password ya access_token padhchha.
    Format (ek line ma ek):
        uid=123456789
        password=YOUR_PASSWORD
        access_token=YOUR_TOKEN     (yo bhare token le priority paunchha)
    '#' le comment. Script ko folder ra current folder dubai ma khojchha."""
    for fp in ([path] if path else _cred_paths()):
        if not os.path.isfile(fp):
            continue
        creds = {}
        try:
            with open(fp, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    creds[k.strip().lower()] = v.strip().strip('"').strip("'")
        except Exception as e:
            print_error(f"{fp} read error: {e}")
            continue
        return creds
    return None


AUTO_RESTART_HOURS = 6   # token expire hune bhayera yati ghanta pachi feri login


async def run_forever(factory):
    """UID/password le chalda: disconnect ya token expire bhayo bhane aafai re-login."""
    while not shutdown_requested:
        started = time.time()
        try:
            await asyncio.wait_for(factory(), timeout=AUTO_RESTART_HOURS * 3600)
            print_warning("Bot stopped — restarting")
        except asyncio.TimeoutError:
            print_info("Token refresh time — re-login gardai")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print_error(f"run error: {e}")
        # login fail bhayo (chadai fail) bhane jhat-jhat retry nagarne
        await asyncio.sleep(5 if time.time() - started > 60 else 30)


async def main_async():
    global shutdown_requested
    creds = read_credentials()
    if creds:
        shutdown_requested = False
        token = creds.get("access_token", "")
        uid = creds.get("uid", "")
        pw = creds.get("password", "")
        if token:
            print_info(f"Using access_token from {CREDENTIALS_FILE}")
            await run_token_account(token)
            return
        if uid.isdigit() and pw:
            print_info(f"Using uid/password from {CREDENTIALS_FILE}")
            await run_forever(lambda: run_guest_account(uid, pw))
            return
        print_warning(f"{CREDENTIALS_FILE} ma valid uid+password ya access_token chhaina — menu khulchha")
    else:
        print_warning(f"{CREDENTIALS_FILE} bhetiyena — menu khulchha")
    await menu_async()


async def menu_async():
    global shutdown_requested

    print_menu()
    try:
        choice = await asyncio.to_thread(input, "Enter choice [1-3]: ")
    except (EOFError, KeyboardInterrupt):
        print()
        return
    choice = choice.strip()

    if choice == "1":
        print()
        try:
            uid = await asyncio.to_thread(input, "Enter UID => ")
            uid = uid.strip()
            if not uid.isdigit():
                print_error("UID must be numbers only")
                return
            password = await asyncio.to_thread(input, "Enter Password => ")
            password = password.strip()
            if not password:
                print_error("Password required")
                return
        except (EOFError, KeyboardInterrupt):
            print()
            return

        shutdown_requested = False
        await run_forever(lambda: run_guest_account(uid, password))

    elif choice == "2":
        print()
        try:
            token = await asyncio.to_thread(input, "Enter Access Token => ")
            token = token.strip()
            if len(token) < 50:
                print_error("Invalid token format")
                return
        except (EOFError, KeyboardInterrupt):
            print()
            return

        shutdown_requested = False
        await run_token_account(token)

    elif choice == "3":
        print_info("Exiting...")
        return
    else:
        print_error("Invalid choice.")
        return


def main():
    global shutdown_requested
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        print()
        print_warning("Shutting down...")
        shutdown_requested = True
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        print_success("Exited.")


if __name__ == "__main__":
    main()