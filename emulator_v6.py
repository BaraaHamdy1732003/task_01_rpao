#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эмулятор учебного процессора  —  ВАРИАНТ 6
=============================================
Архитектура : Гарвардская (отдельная память команд CMEM и память данных DMEM)
Адресность  : двухадресная   (OP  dst, src   =>   dst = dst OP src)
Задание 1   : сумма элементов массива
Задание 2   : свёртка двух массивов, 10 элементов, целые числа со знаком

Что есть в программе (по плану работы из методички):
  1. Программы на ассемблере (см. SAMPLES внизу)
  2. Кодирование команд в 32-битные машинные слова (см. encode()/decode())
  3. Парсер/ассемблер: текст -> токены -> машинные коды в память команд
     (таблица меток, неразрешённые «прыжки вперёд», ошибки с номерами строк)
  4. Эмулятор: цикл  cmd = cmem[pc]; выполнить; pc = pc + 1; выход по HALT
     или по лимиту числа шагов; флаги Z N C V; адресация: регистровая,
     непосредственная, прямая, косвенная и БАЗОВАЯ (база + смещение)
  5. GUI на tkinter: регистры, флаги, память команд, память данных,
     подсветка изменений, пошаговое выполнение

Запуск:
    python emulator_v6.py              - графический интерфейс
    python emulator_v6.py --selftest   - автотесты без GUI
    python emulator_v6.py --run f.asm  - выполнить файл без GUI
"""
import re
import sys

# --------------------------------------------------------------------------
#  Параметры архитектуры
# --------------------------------------------------------------------------
WORD_BITS = 32                 # разрядность регистров, слов данных и команд
MASK = 0xFFFFFFFF
NREGS = 16                     # R0..R15, 32 бита, со знаком (доп. код)
CMEM_SIZE = 256                # память команд (слов по 32 бита)
DMEM_SIZE = 256                # память данных (слов по 32 бита)
IMM_BITS = 15                  # поле immediate/адрес/смещение
IMM_MASK = (1 << IMM_BITS) - 1
IMM_MIN = -(1 << (IMM_BITS - 1))
IMM_MAX = (1 << (IMM_BITS - 1)) - 1

# Формат команды (32 бита):
#   31..26 | 25..23 | 22..19 | 18..15 | 14..0
#    OPCODE |  MODE  |  DST   |  SRC   |  IMM
#     6 бит |  3 бита| 4 бита | 4 бита | 15 бит
OP_SHIFT, MODE_SHIFT, D_SHIFT, S_SHIFT = 26, 23, 19, 15

# Режимы адресации (поле MODE)
M_REG, M_IMM, M_DIR, M_IND, M_BASE = 0, 1, 2, 3, 4
MODE_NAMES = {M_REG: "регистровая", M_IMM: "непосредственная",
              M_DIR: "прямая", M_IND: "косвенная", M_BASE: "базовая (R+смещ.)"}

OPS = [
    ("NOP", 0), ("HALT", 1), ("MOV", 2), ("ST", 3),
    ("ADD", 4), ("SUB", 5), ("MUL", 6), ("AND", 7), ("OR", 8), ("XOR", 9),
    ("CMP", 10), ("SHL", 11), ("SHR", 12),
    ("NOT", 13), ("INC", 14), ("DEC", 15),
    ("JMP", 16), ("JZ", 17), ("JNZ", 18), ("JC", 19), ("JNC", 20),
    ("JN", 21), ("JNN", 22), ("JL", 23), ("JGE", 24), ("JG", 25), ("JLE", 26),
]
OPCODE = dict(OPS)
OPNAME = {c: n for n, c in OPS}
ALU2 = {"ADD", "SUB", "MUL", "AND", "OR", "XOR", "CMP", "SHL", "SHR"}
ALU1 = {"NOT", "INC", "DEC"}
JUMPS = {"JMP", "JZ", "JNZ", "JC", "JNC", "JN", "JNN", "JL", "JGE", "JG", "JLE"}
ALIAS = {"JE": "JZ", "JNE": "JNZ", "JB": "JC", "JAE": "JNC"}

JUMP_COND = {
    "JMP": lambda f: True,
    "JZ":  lambda f: f["Z"] == 1,
    "JNZ": lambda f: f["Z"] == 0,
    "JC":  lambda f: f["C"] == 1,
    "JNC": lambda f: f["C"] == 0,
    "JN":  lambda f: f["N"] == 1,
    "JNN": lambda f: f["N"] == 0,
    "JL":  lambda f: f["N"] != f["V"],
    "JGE": lambda f: f["N"] == f["V"],
    "JG":  lambda f: f["Z"] == 0 and f["N"] == f["V"],
    "JLE": lambda f: f["Z"] == 1 or f["N"] != f["V"],
}


def to_signed(v, bits=WORD_BITS):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


# --------------------------------------------------------------------------
#  Кодирование / декодирование команд
# --------------------------------------------------------------------------
def encode(op, mode, d, s, imm):
    return ((op << OP_SHIFT) | (mode << MODE_SHIFT) | (d << D_SHIFT)
            | (s << S_SHIFT) | (imm & IMM_MASK)) & MASK


def decode(word):
    """Выделение полей: сдвиг вправо и маска (как в методичке)."""
    op = (word >> OP_SHIFT) & 0x3F
    mode = (word >> MODE_SHIFT) & 0x7
    d = (word >> D_SHIFT) & 0xF
    s = (word >> S_SHIFT) & 0xF
    imm = word & IMM_MASK
    return op, mode, d, s, imm


def _fmt_src(mode, s, imm):
    simm = to_signed(imm, IMM_BITS)
    if mode == M_REG:
        return f"R{s}"
    if mode == M_IMM:
        return str(simm)
    if mode == M_DIR:
        return f"[{imm}]"
    if mode == M_IND:
        return f"[R{s}]"
    if mode == M_BASE:
        return f"[R{s}{simm:+d}]"
    return f"?mode{mode}"


def disassemble(word):
    op, mode, d, s, imm = decode(word)
    name = OPNAME.get(op)
    if name is None:
        return f"??? (op={op})"
    if name in ("NOP", "HALT"):
        return name
    if name in JUMPS:
        return f"{name} {imm}"
    if name in ALU1:
        return f"{name} R{d}"
    if name == "ST":
        simm = to_signed(imm, IMM_BITS)
        dst = {M_DIR: f"[{imm}]", M_IND: f"[R{d}]",
               M_BASE: f"[R{d}{simm:+d}]"}.get(mode, "[?]")
        return f"MOV {dst}, R{s}"
    return f"{name} R{d}, {_fmt_src(mode, s, imm)}"


def fields_str(word):
    op, mode, d, s, imm = decode(word)
    return f"{op:06b} {mode:03b} {d:04b} {s:04b} {imm:015b}"


# --------------------------------------------------------------------------
#  Ассемблер: лексер + разбор + таблица меток + неразрешённые ссылки
# --------------------------------------------------------------------------
class AsmError(Exception):
    def __init__(self, msg, line=0):
        super().__init__(msg)
        self.msg = msg
        self.line = line


class AsmFailure(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(f"строка {e.line}: {e.msg}" for e in errors))
        self.errors = errors


class Operand:
    def __init__(self, kind, reg=0, expr=None):
        self.kind, self.reg, self.expr = kind, reg, expr


class Instr:
    def __init__(self, addr, name, line, text):
        self.addr, self.name, self.line, self.text = addr, name, line, text
        self.op = OPCODE[name]
        self.mode = M_REG
        self.d = 0
        self.s = 0
        self.expr = None     # выражение ещё не вычислено (метка не известна)
        self.kind = "simm"   # simm | daddr | jump
        self.imm = 0

    def word(self):
        return encode(self.op, self.mode, self.d, self.s, self.imm)


class Program:
    def __init__(self):
        self.code = []          # машинные слова
        self.data = []          # начальное содержимое памяти данных
        self.instrs = []        # Instr (для листинга)
        self.code_labels = {}
        self.data_labels = {}
        self.consts = {}

    def listing(self):
        lines = []
        for ins in self.instrs:
            w = ins.word()
            lines.append(f"{ins.addr:03d}  {w:08X}  {fields_str(w)}  "
                         f"{disassemble(w):<18} ; {ins.text}")
        return "\n".join(lines)


TERM_RE = re.compile(r"\s*([+-]?)\s*(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+|[A-Za-z_]\w*)")
LABEL_RE = re.compile(r"^([A-Za-z_]\w*)\s*:\s*(.*)$")
REG_RE = re.compile(r"^[Rr](\d+)$")
BASEREG_RE = re.compile(r"^[Rr](\d+)(?!\w)\s*([+-].*)?$")


def eval_expr(text, symbols):
    """Сумма чисел/символов со знаками. Возвращает (значение, [неизвестные символы])."""
    text = text.strip()
    if not text:
        raise ValueError("пустое выражение")
    pos, total, missing, first = 0, 0, [], True
    while pos < len(text):
        m = TERM_RE.match(text, pos)
        if not m:
            raise ValueError(f"не могу разобрать «{text[pos:].strip()}»")
        sign, tok = m.groups()
        if not first and not sign:
            raise ValueError(f"ожидался + или - перед «{tok}»")
        first = False
        if tok[0].isdigit():
            val = int(tok, 0)
        elif tok in symbols:
            val = symbols[tok]
        else:
            val = 0
            missing.append(tok)
        total += -val if sign == "-" else val
        pos = m.end()
    return total, missing


def parse_operand(t):
    t = t.strip()
    if t.startswith("["):
        if not t.endswith("]"):
            raise ValueError("нет закрывающей ]")
        inner = t[1:-1].strip()
        if not inner:
            raise ValueError("пустой адрес в []")
        m = BASEREG_RE.match(inner)
        if m:
            reg = int(m.group(1))
            if reg >= NREGS:
                raise ValueError(f"нет регистра R{reg} (есть R0..R{NREGS-1})")
            rest = (m.group(2) or "").strip()
            return Operand("ind", reg) if not rest else Operand("base", reg, rest)
        return Operand("dir", 0, inner)
    m = REG_RE.match(t)
    if m:
        reg = int(m.group(1))
        if reg >= NREGS:
            raise ValueError(f"нет регистра R{reg} (есть R0..R{NREGS-1})")
        return Operand("reg", reg)
    return Operand("imm", 0, t.lstrip("#"))


class _Assembler:
    def __init__(self):
        self.prog = Program()
        self.symbols = {}
        self.waiting = {}       # имя -> [Instr]  : «таблица неразрешённых ссылок»
        self.errors = []
        self.section = "code"

    # --- символы -----------------------------------------------------
    def define(self, name, value, kind):
        if name.upper() in OPCODE or name.upper() in ALIAS or REG_RE.match(name):
            raise ValueError(f"имя «{name}» зарезервировано")
        if name in self.symbols:
            raise ValueError(f"символ «{name}» уже определён")
        self.symbols[name] = value
        {"code": self.prog.code_labels, "data": self.prog.data_labels,
         "const": self.prog.consts}[kind][name] = value
        # объявили метку -> дописываем адрес во все ранее встреченные переходы
        for ins in self.waiting.pop(name, []):
            self.resolve(ins)

    def resolve(self, ins):
        if ins.expr is None:
            return
        try:
            val, missing = eval_expr(ins.expr, self.symbols)
        except ValueError as e:
            raise AsmError(str(e), ins.line)
        if missing:
            self.waiting.setdefault(missing[0], []).append(ins)
            return
        ins.expr = None
        if ins.kind == "simm" and not IMM_MIN <= val <= IMM_MAX:
            raise AsmError(f"число {val} не влезает в {IMM_BITS} бит со знаком "
                           f"({IMM_MIN}..{IMM_MAX})", ins.line)
        if ins.kind == "daddr" and not 0 <= val < DMEM_SIZE:
            raise AsmError(f"адрес данных {val} вне 0..{DMEM_SIZE-1}", ins.line)
        if ins.kind == "jump" and not 0 <= val < CMEM_SIZE:
            raise AsmError(f"адрес перехода {val} вне 0..{CMEM_SIZE-1}", ins.line)
        ins.imm = val & IMM_MASK

    # --- директивы ---------------------------------------------------
    def directive(self, text):
        parts = text.split(None, 1)
        d = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        data = self.prog.data
        if d in (".data", ".code", ".text"):
            if arg:
                raise ValueError(f"{d} без параметров")
            self.section = "data" if d == ".data" else "code"
        elif d == ".word":
            if self.section != "data":
                raise ValueError(".word допустим только в секции .data")
            if not arg:
                raise ValueError(".word без значений")
            for part in arg.split(","):
                val, missing = eval_expr(part, self.symbols)
                if missing:
                    raise ValueError(f"неизвестный символ «{missing[0]}»")
                if not -(1 << 31) <= val <= MASK:
                    raise ValueError(f"значение {val} не влезает в 32 бита")
                data.append(val & MASK)
            if len(data) > DMEM_SIZE:
                raise ValueError("не хватает памяти данных")
        elif d == ".space":
            if self.section != "data":
                raise ValueError(".space допустим только в секции .data")
            val, missing = eval_expr(arg, self.symbols)
            if missing or val < 0:
                raise ValueError("неверный размер .space")
            data.extend([0] * val)
            if len(data) > DMEM_SIZE:
                raise ValueError("не хватает памяти данных")
        elif d == ".equ":
            if "," not in arg:
                raise ValueError("формат: .equ ИМЯ, значение")
            name, expr = [x.strip() for x in arg.split(",", 1)]
            val, missing = eval_expr(expr, self.symbols)
            if missing:
                raise ValueError(f"неизвестный символ «{missing[0]}»")
            self.define(name, val, "const")
        else:
            raise ValueError(f"неизвестная директива {d}")

    # --- инструкции --------------------------------------------------
    def need(self, ops, n, name):
        if len(ops) != n:
            raise ValueError(f"{name}: нужно операндов: {n}, указано: {len(ops)}")

    def set_src(self, ins, o):
        if o.kind == "reg":
            ins.mode, ins.s = M_REG, o.reg
        elif o.kind == "imm":
            ins.mode, ins.expr, ins.kind = M_IMM, o.expr, "simm"
        elif o.kind == "dir":
            ins.mode, ins.expr, ins.kind = M_DIR, o.expr, "daddr"
        elif o.kind == "ind":
            ins.mode, ins.s = M_IND, o.reg
        elif o.kind == "base":
            ins.mode, ins.s, ins.expr, ins.kind = M_BASE, o.reg, o.expr, "simm"

    def instruction(self, text, lineno):
        if self.section != "code":
            raise ValueError("инструкции пишутся в секции .code")
        parts = text.split(None, 1)
        name = parts[0].upper()
        name = ALIAS.get(name, name)
        if name not in OPCODE or name == "ST":
            raise ValueError(f"неизвестная команда «{parts[0]}»")
        ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 else []
        if any(o == "" for o in ops):
            raise ValueError("пустой операнд")
        addr = len(self.prog.instrs)
        if addr >= CMEM_SIZE:
            raise ValueError("не хватает памяти команд")

        if name in ("NOP", "HALT"):
            self.need(ops, 0, name)
            ins = Instr(addr, name, lineno, text)
        elif name in ALU1:
            self.need(ops, 1, name)
            o = parse_operand(ops[0])
            if o.kind != "reg":
                raise ValueError(f"{name}: операнд должен быть регистром")
            ins = Instr(addr, name, lineno, text)
            ins.d = o.reg
        elif name in JUMPS:
            self.need(ops, 1, name)
            o = parse_operand(ops[0])
            if o.kind != "imm":
                raise ValueError(f"{name}: нужна метка или адрес")
            ins = Instr(addr, name, lineno, text)
            ins.mode, ins.expr, ins.kind = M_IMM, o.expr, "jump"
        else:  # MOV и двухадресные АЛУ-команды
            self.need(ops, 2, name)
            dst, src = parse_operand(ops[0]), parse_operand(ops[1])
            if dst.kind == "reg":
                ins = Instr(addr, name, lineno, text)
                ins.d = dst.reg
                self.set_src(ins, src)
            elif dst.kind in ("dir", "ind", "base") and name == "MOV":
                if src.kind != "reg":
                    raise ValueError("память-память запрещена: источник при записи "
                                     "в память должен быть регистром")
                ins = Instr(addr, "ST", lineno, text)
                ins.s = src.reg
                if dst.kind == "dir":
                    ins.mode, ins.expr, ins.kind = M_DIR, dst.expr, "daddr"
                elif dst.kind == "ind":
                    ins.mode, ins.d = M_IND, dst.reg
                else:
                    ins.mode, ins.d, ins.expr, ins.kind = M_BASE, dst.reg, dst.expr, "simm"
            else:
                raise ValueError(f"{name}: первый операнд должен быть регистром")
        self.prog.instrs.append(ins)
        self.resolve(ins)

    # --- основной проход ---------------------------------------------
    def run(self, source):
        for lineno, raw in enumerate(source.splitlines(), 1):
            text = raw.split(";", 1)[0].strip()
            if not text:
                continue
            try:
                m = LABEL_RE.match(text)
                if m:
                    label, text = m.group(1), m.group(2).strip()
                    if self.section == "code":
                        self.define(label, len(self.prog.instrs), "code")
                    else:
                        self.define(label, len(self.prog.data), "data")
                if not text:
                    continue
                if text.startswith("."):
                    self.directive(text)
                else:
                    self.instruction(text, lineno)
            except AsmError as e:
                self.errors.append(e)
            except ValueError as e:
                self.errors.append(AsmError(str(e), lineno))
        for name, lst in self.waiting.items():
            for ins in lst:
                self.errors.append(AsmError(f"неопределённый символ «{name}»", ins.line))
        if self.errors:
            self.errors.sort(key=lambda e: e.line)
            raise AsmFailure(self.errors)
        self.prog.code = [i.word() for i in self.prog.instrs]
        return self.prog


def assemble(source):
    return _Assembler().run(source)


# --------------------------------------------------------------------------
#  Эмулятор процессора
# --------------------------------------------------------------------------
class CPUError(Exception):
    pass


class StepInfo:
    def __init__(self):
        self.pc = 0
        self.cmd = 0
        self.text = ""
        self.regs = set()
        self.mem = set()
        self.halted = False


class CPU:
    def __init__(self, max_steps=100000):
        self.max_steps = max_steps
        self.program = None
        self.cmem = [0] * CMEM_SIZE          # Гарвард: память команд ...
        self.reset()

    def load(self, prog):
        self.program = prog
        self.cmem = [0] * CMEM_SIZE
        self.cmem[:len(prog.code)] = prog.code
        self.reset()

    def reset(self):
        self.regs = [0] * NREGS
        self.pc = 0
        self.cmd = 0
        self.flags = {"Z": 0, "N": 0, "C": 0, "V": 0}
        self.dmem = [0] * DMEM_SIZE          # ... и отдельная память данных
        if self.program:
            self.dmem[:len(self.program.data)] = self.program.data
        self.halted = False
        self.steps = 0

    # --- доступ к памяти данных -------------------------------------
    def rd(self, addr):
        if not 0 <= addr < DMEM_SIZE:
            raise CPUError(f"чтение: адрес данных {addr} вне 0..{DMEM_SIZE-1} (PC={self.pc})")
        return self.dmem[addr]

    def wr(self, addr, val, info):
        if not 0 <= addr < DMEM_SIZE:
            raise CPUError(f"запись: адрес данных {addr} вне 0..{DMEM_SIZE-1} (PC={self.pc})")
        val &= MASK
        if self.dmem[addr] != val:
            info.mem.add(addr)
        self.dmem[addr] = val

    def set_reg(self, r, val, info):
        val &= MASK
        if self.regs[r] != val:
            info.regs.add(r)
        self.regs[r] = val

    def src_value(self, mode, s, imm):
        simm = to_signed(imm, IMM_BITS)
        if mode == M_REG:
            return self.regs[s]
        if mode == M_IMM:
            return simm & MASK
        if mode == M_DIR:
            return self.rd(imm)
        if mode == M_IND:
            return self.rd(to_signed(self.regs[s]))
        if mode == M_BASE:
            return self.rd(to_signed(self.regs[s]) + simm)
        raise CPUError(f"неизвестный режим адресации {mode}")

    # --- АЛУ ------------------------------------------------------------
    def alu(self, name, a, b):
        sa, sb = to_signed(a), to_signed(b)
        c = v = 0
        if name in ("ADD", "INC"):
            full = a + b
            res = full & MASK
            c = full >> WORD_BITS
            v = int(sa + sb != to_signed(res))
        elif name in ("SUB", "CMP", "DEC"):
            res = (a - b) & MASK
            c = int(a < b)
            v = int(sa - sb != to_signed(res))
        elif name == "MUL":
            full = sa * sb
            res = full & MASK
            c = v = int(full != to_signed(res))
        elif name == "AND":
            res = a & b
        elif name == "OR":
            res = a | b
        elif name == "XOR":
            res = a ^ b
        elif name == "NOT":
            res = ~a & MASK
        elif name == "SHL":
            n = b & 31
            res = (a << n) & MASK
            c = (a >> (WORD_BITS - n)) & 1 if n else 0
        elif name == "SHR":                    # арифметический сдвиг
            n = b & 31
            res = (sa >> n) & MASK
            c = (a >> (n - 1)) & 1 if n else 0
        else:
            raise CPUError(f"АЛУ: неизвестная операция {name}")
        self.flags.update(Z=int(res == 0), N=res >> (WORD_BITS - 1), C=c, V=v)
        return res

    # --- один такт: выборка, декодирование, исполнение ------------------
    def step(self):
        if self.halted:
            raise CPUError("процессор остановлен командой HALT")
        if not 0 <= self.pc < CMEM_SIZE:
            raise CPUError(f"PC={self.pc} вне памяти команд")
        info = StepInfo()
        info.pc = self.pc
        self.cmd = self.cmem[self.pc]                       # cmd = cmem[pc]
        info.cmd = self.cmd
        info.text = disassemble(self.cmd)
        op, mode, d, s, imm = decode(self.cmd)
        name = OPNAME.get(op)
        if name is None:
            raise CPUError(f"недопустимый код операции {op} по адресу {self.pc}")
        nxt = self.pc + 1

        if name == "NOP":
            pass
        elif name == "HALT":
            self.halted = True
            info.halted = True
            nxt = self.pc
        elif name == "MOV":
            self.set_reg(d, self.src_value(mode, s, imm), info)
        elif name == "ST":
            val = self.regs[s]
            if mode == M_DIR:
                addr = imm
            elif mode == M_IND:
                addr = to_signed(self.regs[d])
            elif mode == M_BASE:
                addr = to_signed(self.regs[d]) + to_signed(imm, IMM_BITS)
            else:
                raise CPUError("ST: недопустимый режим адресации")
            self.wr(addr, val, info)
        elif name in ALU2:
            res = self.alu(name, self.regs[d], self.src_value(mode, s, imm))
            if name != "CMP":
                self.set_reg(d, res, info)
        elif name in ALU1:
            b = 1 if name in ("INC", "DEC") else 0
            self.set_reg(d, self.alu(name, self.regs[d], b), info)
        elif name in JUMPS:
            if JUMP_COND[name](self.flags):
                nxt = imm
        self.pc = nxt
        self.steps += 1
        return info

    def run(self, max_steps=None):
        limit = self.max_steps if max_steps is None else max_steps
        while not self.halted:
            if self.steps >= limit:
                return "limit"
            self.step()
        return "halt"


# --------------------------------------------------------------------------
#  Примеры программ (задание варианта 6)
# --------------------------------------------------------------------------
SAMPLE_SUM = """\
; ЗАДАНИЕ 1. Сумма элементов массива (10 целых чисел со знаком)
; Вариант 6: двухадресная, Гарвардская архитектура
; R0 - сумма, R1 - указатель на элемент, R2 - счётчик
.equ N, 10

.data
ARR:    .word 5, -3, 12, 7, -8, 20, -1, 4, 9, -6
RES:    .word 0

.code
        MOV R0, 0          ; сумма = 0            (непосредственная)
        MOV R1, ARR        ; R1 = адрес массива   (непосредственная)
        MOV R2, N          ; счётчик = 10
loop:   ADD R0, [R1]       ; R0 += dmem[R1]       (косвенная)
        INC R1
        DEC R2
        JNZ loop           ; пока счётчик != 0
        MOV [RES], R0      ; dmem[RES] = R0       (прямая)
        HALT               ; ожидаемый результат: 39
"""

SAMPLE_DOT = """\
; ЗАДАНИЕ 2 (вариант «скалярная свёртка»): sum(A[i]*B[i]), i = 0..9
; R0 - накопитель, R1 - индекс i, R2 - временный
.equ N, 10

.data
A:      .word 1, 2, 3, -4, 5, -6, 7, 8, -9, 10
B:      .word 2, -1, 3, 0, -2, 4, 1, -3, 5, -1
RES:    .word 0

.code
        MOV R0, 0
        MOV R1, 0
loop:   MOV R2, [R1+A]     ; R2 = A[i]            (базовая)
        MUL R2, [R1+B]     ; R2 *= B[i]           (базовая)
        ADD R0, R2
        INC R1
        CMP R1, N
        JL  loop           ; знаковое i < N
        MOV [RES], R0
        HALT               ; ожидаемый результат: -97
"""

SAMPLE_CONV = """\
; ЗАДАНИЕ 2. Полная (линейная) свёртка двух массивов по 10 элементов:
;     C[k] = sum_{i} A[i] * B[k-i],  k = 0..18,  0 <= i,k-i <= 9
; R0 - k, R1 - i, R3 - накопитель C[k], R4 - j = k - i, R5 - временный
.equ N, 10
.equ M, 19                 ; длина результата 2*N-1

.data
A:      .word 1, 2, 3, -4, 5, -6, 7, 8, -9, 10
B:      .word 2, -1, 3, 0, -2, 4, 1, -3, 5, -1
C:      .space M

.code
        MOV R0, 0                ; k = 0
kloop:  MOV R3, 0                ; acc = 0
        MOV R1, 0                ; i = 0
iloop:  MOV R4, R0
        SUB R4, R1               ; j = k - i
        JL  skip                 ; j < 0  -> пропустить
        CMP R4, N
        JGE skip                 ; j >= N -> пропустить
        MOV R5, [R1+A]           ; A[i]
        MUL R5, [R4+B]           ; * B[j]
        ADD R3, R5
skip:   INC R1
        CMP R1, N
        JL  iloop
        MOV [R0+C], R3           ; C[k] = acc
        INC R0
        CMP R0, M
        JL  kloop
        HALT
"""

SAMPLES = {
    "Задание 1: сумма элементов массива": SAMPLE_SUM,
    "Задание 2: свёртка (полная, 19 элементов)": SAMPLE_CONV,
    "Задание 2: свёртка (скалярная, сумма A[i]*B[i])": SAMPLE_DOT,
}


# --------------------------------------------------------------------------
#  Консольные режимы
# --------------------------------------------------------------------------
def dump(cpu):
    print(f"PC={cpu.pc}  steps={cpu.steps}  flags={cpu.flags}")
    for i in range(NREGS):
        print(f"  R{i:<2} = {to_signed(cpu.regs[i]):>11}   0x{cpu.regs[i]:08X}")
    if cpu.program:
        for name, addr in cpu.program.data_labels.items():
            print(f"  [{name}] @ {addr}")
    used = len(cpu.program.data) if cpu.program else 0
    print("  DMEM:", [to_signed(x) for x in cpu.dmem[:used]])


def selftest():
    def conv(a, b):
        return [sum(a[i] * b[k - i] for i in range(10) if 0 <= k - i < 10)
                for k in range(19)]
    A = [1, 2, 3, -4, 5, -6, 7, 8, -9, 10]
    B = [2, -1, 3, 0, -2, 4, 1, -3, 5, -1]

    p = assemble(SAMPLE_SUM)
    cpu = CPU(); cpu.load(p)
    assert cpu.run() == "halt"
    res = p.data_labels["RES"]
    assert to_signed(cpu.dmem[res]) == 39, cpu.dmem[res]
    print("OK  сумма            :", to_signed(cpu.dmem[res]))

    p = assemble(SAMPLE_DOT)
    cpu = CPU(); cpu.load(p)
    assert cpu.run() == "halt"
    expect = sum(x * y for x, y in zip(A, B))
    assert to_signed(cpu.dmem[p.data_labels["RES"]]) == expect
    print("OK  скалярная свёртка:", expect)

    p = assemble(SAMPLE_CONV)
    cpu = CPU(); cpu.load(p)
    assert cpu.run() == "halt"
    c0 = p.data_labels["C"]
    got = [to_signed(x) for x in cpu.dmem[c0:c0 + 19]]
    assert got == conv(A, B), (got, conv(A, B))
    print("OK  полная свёртка   :", got, "шагов:", cpu.steps)

    # кодирование/декодирование и дизассемблер
    for src in ["ADD R3, [R7+5]", "MOV [R2-1], R4", "MOV R1, -7",
                "CMP R1, [12]", "INC R9", "MOV [40], R0"]:
        w = assemble(src).code[0]
        assert encode(*decode(w)) == w
    assert disassemble(assemble("ADD R3, [R7+5]").code[0]) == "ADD R3, [R7+5]"

    # прыжок вперёд (неразрешённая метка)
    q = assemble("JMP end\nNOP\nend: HALT")
    assert q.instrs[0].imm == 2
    # ошибки
    for bad, frag in [("FOO R1", "неизвестная"), ("ADD R1", "операндов"),
                      ("MOV [R1], [R2]", "регистром"), ("JMP nowhere", "неопределённый"),
                      ("MOV R16, 1", "нет регистра"), ("MOV R1, 99999", "не влезает")]:
        try:
            assemble(bad)
            raise AssertionError("ожидалась ошибка: " + bad)
        except AsmFailure as e:
            assert frag in str(e), (bad, str(e))
    # лимит шагов (зацикливание)
    cpu = CPU(max_steps=50); cpu.load(assemble("l: JMP l"))
    assert cpu.run() == "limit"
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ")


def cli_run(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        prog = assemble(src)
    except AsmFailure as e:
        for er in e.errors:
            print(f"строка {er.line}: {er.msg}")
        sys.exit(1)
    print(prog.listing())
    cpu = CPU(); cpu.load(prog)
    print("результат:", cpu.run())
    dump(cpu)


# --------------------------------------------------------------------------
#  Графический интерфейс
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Эмулятор процессора — вариант 6 (двухадресная, Гарвард)")
            self.geometry("1450x880")
            self.cpu = CPU()
            self.prog = None
            self.running = False
            self.ch_regs, self.ch_mem = set(), set()
            self._dcache = {}
            self.data_names = {}
            self._style()
            self._build()
            self.load_sample(list(SAMPLES)[0])

        # ---------- оформление ----------
        def _style(self):
            st = ttk.Style(self)

            def fixed_map(opt):  # обход бага цветов Treeview в Tk 8.6.9
                return [e for e in st.map("Treeview", query_opt=opt)
                        if e[:2] != ("!disabled", "!selected")]
            st.map("Treeview", foreground=fixed_map("foreground"),
                   background=fixed_map("background"))
            st.configure("Treeview", rowheight=20, font=("Consolas", 10))
            st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        def _tree(self, parent, cols, widths, heads, height=16):
            fr = ttk.Frame(parent)
            t = ttk.Treeview(fr, columns=cols, show="headings", height=height)
            for c, w, h in zip(cols, widths, heads):
                t.heading(c, text=h)
                t.column(c, width=w, anchor="w" if c in ("asm", "sym", "bin") else "e")
            sb = ttk.Scrollbar(fr, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=sb.set)
            t.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            t.tag_configure("pc", background="#ffe27a")
            t.tag_configure("chg", background="#a8f0b0")
            return fr, t

        def _build(self):
            bar = ttk.Frame(self, padding=4)
            bar.pack(fill="x")
            self.sample_var = tk.StringVar()
            cb = ttk.Combobox(bar, textvariable=self.sample_var, state="readonly",
                              values=list(SAMPLES), width=48)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.load_sample(self.sample_var.get()))
            ttk.Button(bar, text="Открыть…", command=self.open_file).pack(side="left", padx=2)
            ttk.Button(bar, text="Сохранить…", command=self.save_file).pack(side="left", padx=2)
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
            ttk.Button(bar, text="Ассемблировать", command=self.do_assemble).pack(side="left", padx=2)
            ttk.Button(bar, text="Шаг (F8)", command=self.do_step_btn).pack(side="left", padx=2)
            self.run_btn = ttk.Button(bar, text="Выполнить (F5)", command=self.toggle_run)
            self.run_btn.pack(side="left", padx=2)
            ttk.Button(bar, text="Сброс", command=self.do_reset).pack(side="left", padx=2)
            ttk.Button(bar, text="Экспорт кода…", command=self.export).pack(side="left", padx=2)
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
            ttk.Label(bar, text="мс/шаг:").pack(side="left")
            self.speed = tk.IntVar(value=0)
            ttk.Scale(bar, from_=0, to=500, variable=self.speed, length=110).pack(side="left")
            ttk.Label(bar, text="  макс. шагов:").pack(side="left")
            self.maxsteps = tk.StringVar(value="100000")
            ttk.Entry(bar, textvariable=self.maxsteps, width=8).pack(side="left")
            self.bind("<F5>", lambda e: self.toggle_run())
            self.bind("<F8>", lambda e: self.do_step_btn())

            # нижний журнал
            self.log = tk.Text(self, height=9, font=("Consolas", 10), bg="#1e1e1e",
                               fg="#dcdcdc", state="disabled")
            self.log.pack(side="bottom", fill="x")
            self.log.tag_configure("err", foreground="#ff7b72")
            self.log.tag_configure("ok", foreground="#7ee787")

            body = ttk.PanedWindow(self, orient="horizontal")
            body.pack(fill="both", expand=True)

            left = ttk.LabelFrame(body, text="Исходный код (ассемблер)")
            self.editor = tk.Text(left, font=("Consolas", 11), undo=True, width=50, wrap="none")
            esb = ttk.Scrollbar(left, command=self.editor.yview)
            self.editor.configure(yscrollcommand=esb.set)
            self.editor.pack(side="left", fill="both", expand=True)
            esb.pack(side="right", fill="y")
            self.editor.tag_configure("errline", background="#ffb3b3")
            body.add(left, weight=2)

            right = ttk.Frame(body)
            body.add(right, weight=5)

            st = ttk.Frame(right, padding=4)
            st.pack(fill="x")
            self.pc_lbl = ttk.Label(st, text="PC: 0", font=("Consolas", 12, "bold"))
            self.pc_lbl.pack(side="left", padx=8)
            self.ir_lbl = ttk.Label(st, text="IR: -", font=("Consolas", 12))
            self.ir_lbl.pack(side="left", padx=8)
            self.steps_lbl = ttk.Label(st, text="шагов: 0", font=("Consolas", 12))
            self.steps_lbl.pack(side="left", padx=8)
            self.flag_lbls = {}
            for f in "ZNCV":
                l = tk.Label(st, text=f"{f}=0", font=("Consolas", 12, "bold"), width=5,
                             bg="#dddddd", relief="groove")
                l.pack(side="left", padx=2)
                self.flag_lbls[f] = l
            self.state_lbl = ttk.Label(st, text="", font=("Segoe UI", 10, "bold"))
            self.state_lbl.pack(side="left", padx=10)

            panes = ttk.PanedWindow(right, orient="horizontal")
            panes.pack(fill="both", expand=True)

            lf = ttk.LabelFrame(panes, text="Регистры (32 бита)")
            fr2, self.regs_tree = self._tree(lf, ("r", "dec", "hex"), (45, 90, 90),
                                             ("Рег.", "Знак.", "Hex"))
            for i in range(NREGS):
                self.regs_tree.insert("", "end", iid=f"r{i}", values=(f"R{i}", 0, "0x00000000"))
            fr2.pack(fill="both", expand=True)
            panes.add(lf, weight=2)

            lc = ttk.LabelFrame(panes, text="Память команд CMEM (32 бита)")
            fr3, self.cmem_tree = self._tree(
                lc, ("addr", "hex", "bin", "asm"), (40, 75, 235, 140),
                ("Адр", "Hex", "OP     MODE DST  SRC  IMM", "Команда"), height=20)
            fr3.pack(fill="both", expand=True)
            panes.add(lc, weight=5)

            ld = ttk.LabelFrame(panes, text="Память данных DMEM (32 бита)")
            fr4, self.dmem_tree = self._tree(
                ld, ("addr", "sym", "dec", "hex"), (40, 50, 75, 90),
                ("Адр", "Имя", "Знак.", "Hex"), height=20)
            for i in range(DMEM_SIZE):
                self.dmem_tree.insert("", "end", iid=f"d{i}", values=(i, "", 0, "0x00000000"))
            fr4.pack(fill="both", expand=True)
            panes.add(ld, weight=3)

        # ---------- вспомогательные ----------
        def say(self, msg, tag=None):
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n", tag)
            self.log.see("end")
            self.log.configure(state="disabled")

        def load_sample(self, name):
            self.sample_var.set(name)
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", SAMPLES[name])
            self.do_assemble()

        def open_file(self):
            p = filedialog.askopenfilename(filetypes=[("Assembler", "*.asm *.txt"), ("Все", "*.*")])
            if p:
                with open(p, encoding="utf-8") as f:
                    self.editor.delete("1.0", "end")
                    self.editor.insert("1.0", f.read())
                self.do_assemble()

        def save_file(self):
            p = filedialog.asksaveasfilename(defaultextension=".asm")
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(self.editor.get("1.0", "end"))

        def export(self):
            if not self.prog:
                return messagebox.showinfo("Экспорт", "Сначала ассемблируйте программу")
            p = filedialog.asksaveasfilename(defaultextension=".txt")
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write("ADDR  HEX       OP     MODE DST  SRC  IMM              COMMAND\n")
                    f.write(self.prog.listing() + "\n")
                self.say(f"Листинг сохранён: {p}", "ok")

        # ---------- действия ----------
        def do_assemble(self):
            self.running = False
            self.run_btn.configure(text="Выполнить (F5)")
            self.editor.tag_remove("errline", "1.0", "end")
            try:
                self.prog = assemble(self.editor.get("1.0", "end"))
            except AsmFailure as e:
                self.prog = None
                for er in e.errors:
                    self.say(f"ОШИБКА, строка {er.line}: {er.msg}", "err")
                    self.editor.tag_add("errline", f"{er.line}.0", f"{er.line}.end")
                return
            self.cpu.load(self.prog)
            try:
                self.cpu.max_steps = int(self.maxsteps.get())
            except ValueError:
                pass
            self.data_names = {a: n for n, a in self.prog.data_labels.items()}
            self._dcache.clear()
            self.cmem_tree.delete(*self.cmem_tree.get_children())
            for ins in self.prog.instrs:
                w = ins.word()
                self.cmem_tree.insert("", "end", iid=f"c{ins.addr}",
                                      values=(ins.addr, f"{w:08X}", fields_str(w), disassemble(w)))
            self.say(f"Ассемблирование успешно: команд {len(self.prog.code)}, "
                     f"слов данных {len(self.prog.data)}. Метки кода: {self.prog.code_labels}; "
                     f"данных: {self.prog.data_labels}", "ok")
            self.ch_regs.clear(); self.ch_mem.clear()
            self.refresh()

        def do_reset(self):
            self.running = False
            self.run_btn.configure(text="Выполнить (F5)")
            if self.prog:
                self.cpu.reset()
                self.ch_regs.clear(); self.ch_mem.clear()
                self._dcache.clear()
                self.say("Сброс: PC=0, регистры и память данных восстановлены")
                self.refresh()

        def _read_limit(self):
            try:
                self.cpu.max_steps = max(1, int(self.maxsteps.get()))
            except ValueError:
                pass

        def _one_step(self):
            """True - можно продолжать."""
            try:
                info = self.cpu.step()
            except CPUError as e:
                self.say(f"ОШИБКА ВЫПОЛНЕНИЯ: {e}", "err")
                return False
            self.ch_regs |= info.regs
            self.ch_mem |= info.mem
            if info.halted:
                self.say(f"HALT: программа завершена, шагов: {self.cpu.steps}", "ok")
                return False
            if self.cpu.steps >= self.cpu.max_steps:
                self.say(f"Остановка по лимиту шагов ({self.cpu.max_steps})", "err")
                return False
            return True

        def do_step_btn(self):
            if not self.prog:
                return
            self.running = False
            self.run_btn.configure(text="Выполнить (F5)")
            self._read_limit()
            self.ch_regs.clear(); self.ch_mem.clear()
            if not self.cpu.halted:
                self._one_step()
            self.refresh()

        def toggle_run(self):
            if not self.prog:
                return
            if self.running:
                self.running = False
                self.run_btn.configure(text="Выполнить (F5)")
                return
            if self.cpu.halted:
                return
            self._read_limit()
            self.running = True
            self.run_btn.configure(text="Пауза (F5)")
            self._tick()

        def _tick(self):
            if not self.running:
                return
            delay = int(self.speed.get())
            n = 1 if delay > 0 else 500
            self.ch_regs.clear(); self.ch_mem.clear()
            for _ in range(n):
                if not self._one_step():
                    self.running = False
                    break
            if not self.running:
                self.run_btn.configure(text="Выполнить (F5)")
            self.refresh()
            if self.running:
                self.after(max(delay, 1), self._tick)

        # ---------- обновление экрана ----------
        def refresh(self):
            c = self.cpu
            for i in range(NREGS):
                self.regs_tree.item(f"r{i}", values=(f"R{i}", to_signed(c.regs[i]),
                                                     f"0x{c.regs[i]:08X}"),
                                    tags=("chg",) if i in self.ch_regs else ())
            for i in range(DMEM_SIZE):
                v = c.dmem[i]
                tag = ("chg",) if i in self.ch_mem else ()
                key = (v, tag)
                if self._dcache.get(i) != key:
                    self._dcache[i] = key
                    self.dmem_tree.item(f"d{i}", values=(i, self.data_names.get(i, ""),
                                                         to_signed(v), f"0x{v:08X}"), tags=tag)
            if self.ch_mem:
                self.dmem_tree.see(f"d{min(self.ch_mem)}")
            if self.prog:
                for ins in self.prog.instrs:
                    self.cmem_tree.item(f"c{ins.addr}", tags=("pc",) if ins.addr == c.pc else ())
                if c.pc < len(self.prog.instrs):
                    self.cmem_tree.see(f"c{c.pc}")
            self.pc_lbl.configure(text=f"PC: {c.pc}")
            self.ir_lbl.configure(text=f"IR: {c.cmd:08X}  {disassemble(c.cmd) if c.steps else '-'}")
            self.steps_lbl.configure(text=f"шагов: {c.steps}")
            for f, l in self.flag_lbls.items():
                l.configure(text=f"{f}={c.flags[f]}", bg="#ffcf70" if c.flags[f] else "#dddddd")
            self.state_lbl.configure(text="ОСТАНОВЛЕН (HALT)" if c.halted else "")

    App().mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--run" in sys.argv:
        cli_run(sys.argv[sys.argv.index("--run") + 1])
    else:
        run_gui()
