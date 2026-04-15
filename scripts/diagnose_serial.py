"""Quick diagnostic: replicate step 4 + step 5 flow exactly."""
import time

import serial

PORT = "COM3"
BAUDRATE = 9600
COMMAND = "00005 2"

print("=== Test 1: WITH \\r\\n line ending (what guided wizard does) ===")
conn = serial.Serial(port=PORT, baudrate=BAUDRATE, bytesize=8,
                     parity=serial.PARITY_NONE, stopbits=1, timeout=2.0)
conn.dtr = True
conn.rts = True
print(f"Opened {PORT}, DTR=True, RTS=True, timeout=2.0")
conn.reset_input_buffer()
print("Input buffer reset")

print("Sleeping 2s (pre-send delay)...")
time.sleep(2.0)

payload = (COMMAND + "\r\n").encode("ascii")
conn.write(payload)
print(f"Sent: {payload!r} ({len(payload)} bytes)")

print("Sleeping 0.5s (post-send delay)...")
time.sleep(0.5)

print("Reading with in_waiting for 5 seconds...")
buf = b""
t_start = time.monotonic()
sample_count = 0
while time.monotonic() - t_start < 5.0:
    chunk = conn.read(conn.in_waiting or 1)
    if chunk:
        buf += chunk
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            if line:
                sample_count += 1
                if sample_count <= 5:
                    print(f"  Line {sample_count}: {line!r}")

print(f"Total lines parsed: {sample_count}")
print(f"Remaining buffer: {buf[:80]!r}")
conn.close()
print("Closed.\n")

# Wait for device to reset
print("Waiting 3s between tests...")
time.sleep(3.0)

print("=== Test 2: WITHOUT line ending (what debug-raw does) ===")
conn = serial.Serial(port=PORT, baudrate=BAUDRATE, bytesize=8,
                     parity=serial.PARITY_NONE, stopbits=1, timeout=1.0)
conn.dtr = True
conn.rts = True
print(f"Opened {PORT}, DTR=True, RTS=True, timeout=1.0")

print("Sleeping 2s (pre-send delay)...")
time.sleep(2.0)

conn.reset_input_buffer()
print("Input buffer reset")

payload = COMMAND.encode("ascii")
conn.write(payload)
print(f"Sent: {payload!r} ({len(payload)} bytes)")

print("Reading with conn.read(1024) for 5 seconds...")
buf = b""
t_start = time.monotonic()
sample_count = 0
while time.monotonic() - t_start < 5.0:
    chunk = conn.read(1024)
    if chunk:
        buf += chunk
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            if line:
                sample_count += 1
                if sample_count <= 5:
                    print(f"  Line {sample_count}: {line!r}")

print(f"Total lines parsed: {sample_count}")
conn.close()
print("Closed.")
