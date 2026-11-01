# task_01_rpao
# Processor Emulator: Variant 6

A teaching emulator of a simple processor, with a built-in assembler and a graphical interface.

| | |
|---|---|
| **Variant** | 6 |
| **Architecture** | Harvard (separate instruction memory and data memory) |
| **Addressing** | Two-address: `OP dst, src` means `dst = dst OP src` |
| **Task 1** | Sum of the elements of an array |
| **Task 2** | Convolution of two arrays, 10 elements, signed integers |

## Files

| File | Purpose |
|---|---|
| `emulator_v6.py` | The whole program: assembler, processor model, GUI, self-tests |
| `REPORT_v6.md` | Report: program design, instruction encoding, machine code listings (in Russian) |
| `README.md` | This file |

## Requirements

- Python 3.8 or newer
- `tkinter` (included in the standard Python installer for Windows and macOS; on Linux install `python3-tk`)
- No other packages are needed

## How to run

```
python emulator_v6.py              # graphical interface
python emulator_v6.py --selftest   # automatic tests, no GUI
python emulator_v6.py --run prog.asm   # run an assembly file in the console
```

## Using the GUI

1. Pick a program from the drop-down at the top (or write your own in the left editor, or use **Открыть...** to load a `.asm` file).
2. Click **Ассемблировать**. The program is parsed and written into instruction memory as machine code. Errors are listed at the bottom with line numbers, and the faulty line is highlighted in red.
3. Click **Шаг (F8)** to execute one instruction, or **Выполнить (F5)** to run until `HALT`. Click again to pause. **Сброс** returns to the start.

What the window shows:

| Area | Meaning |
|---|---|
| PC / IR / шагов | Program counter, current instruction (hex and text), number of executed steps |
| Z N C V | Flags (highlighted orange when set) |
| Регистры | R0 to R15, signed decimal and hex |
| Память команд (CMEM) | Machine code: address, hex, bit fields, disassembly. The yellow row is the next instruction |
| Память данных (DMEM) | 256 data words with the names of arrays. Green cells changed in the last step |
| Log | Assembly result, errors, `HALT` message, step-limit message |

Other controls: **мс/шаг** slider (slows execution down so you can watch it), **макс. шагов** (step limit that prevents endless loops), **Экспорт кода...** (saves the machine code listing), **Сохранить...** (saves your assembly source).

## Processor model

- **Registers:** 16 general registers `R0` to `R15`, 32 bits, signed (two's complement).
- **Instruction memory (CMEM):** 256 words of 32 bits.
- **Data memory (DMEM):** 256 words of 32 bits. Reads and writes outside 0..255 stop the program with an error.
- **Flags:** `Z` zero, `N` negative, `C` carry or borrow, `V` signed overflow.
- **Execution cycle:** `cmd = cmem[pc]`, decode the fields, execute, `pc = pc + 1` (or a jump). The program stops on `HALT` or when the step limit is reached.

## Assembly language

```
; comment
.equ N, 10              ; constant

.data                   ; data section (goes into data memory)
ARR:  .word 5, -3, 12   ; label + values
RES:  .word 0
BUF:  .space 19         ; 19 zero words

.code                   ; code section (goes into instruction memory)
loop: ADD R0, [R1]      ; label + instruction
      JNZ loop
      HALT
```

- Mnemonics and register names are not case-sensitive. Labels are case-sensitive.
- Numbers: decimal, `0x1F` hex, `0b101` binary. Expressions with `+` and `-` are allowed (`ARR+2`).
- Labels may be used before they are defined (forward jumps are resolved when the label appears).

### Operand types (addressing modes)

| Syntax | Mode | Value of the operand |
|---|---|---|
| `R3` | register | contents of R3 |
| `5`, `-7`, `N`, `ARR` | immediate | the number itself (or the address of a label) |
| `[RES]`, `[12]` | direct | `DMEM[address]` |
| `[R1]` | indirect | `DMEM[R1]` |
| `[R1+A]`, `[R4-1]` | base + offset | `DMEM[R1 + offset]` |

Writing to memory is done with `MOV [..], Rn`. The source must be a register (memory-to-memory moves do not exist).

### Instruction set

| Group | Instructions |
|---|---|
| Control | `NOP`, `HALT` |
| Data transfer | `MOV dst, src` |
| Arithmetic | `ADD`, `SUB`, `MUL`, `INC R`, `DEC R` |
| Logic and shifts | `AND`, `OR`, `XOR`, `NOT R`, `SHL`, `SHR` (arithmetic) |
| Compare | `CMP a, b` (sets flags only, changes no register) |
| Jumps | `JMP`, `JZ`, `JNZ`, `JC`, `JNC`, `JN`, `JNN` and signed comparisons `JL`, `JGE`, `JG`, `JLE` (aliases: `JE`, `JNE`, `JB`, `JAE`) |

Limits: immediate values and offsets must fit in 15 bits (-16384..16383).

## Machine instruction format (32 bits)

```
 31..26 | 25..23 | 22..19 | 18..15 | 14..0
 OPCODE |  MODE  |  DST   |  SRC   |  IMM
 6 bits | 3 bits | 4 bits | 4 bits | 15 bits
```

- `MODE`: 0 register, 1 immediate, 2 direct, 3 indirect, 4 base + offset
- `IMM`: number, address or offset; for jumps it is the absolute target address

Example: `ADD R0, [R1]` is `000100 011 0000 0001 000000000000000` which is `0x11808000`. The full opcode table and the machine code of both tasks are in `REPORT_v6.md`.

## Built-in programs

| Program | What it does | Expected result |
|---|---|---|
| Task 1: sum | Adds 10 signed numbers | `RES = 39` (45 steps) |
| Task 2: convolution (full) | `C[k] = sum A[i]*B[k-i]`, 19 results, using two nested loops and base addressing | `[2, 3, 7, -5, 21, -29, 38, 10, -29, 81, -45, -2, 104, -104, 48, 70, -83, 59, -10]` (1846 steps) |
| Task 2: convolution (scalar) | `sum A[i]*B[i]` | `-97` |

The word "convolution" can mean either the full 19-element result or the single sum of products, so both versions are included.

## Tests

`python emulator_v6.py --selftest` checks all three programs against a Python calculation, the encode/decode round trip, forward jumps, assembler error messages, and the step limit.

## Known limitations

- No division instruction (the course text notes that division is usually done in software).
- No interrupts, stack, or `CALL`/`RET`.
- Executes one instruction per step; pipelining and cache are not modeled.