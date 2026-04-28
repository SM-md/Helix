import serial
import time
import threading
import sys

# --- CONFIGURATION ---
SERIAL_PORT = '/dev/ttyUSB0' # Change to /dev/ttyACM0 if needed
BAUD_RATE = 115200

# Connect to the ESP32
try:
    print(f"Connecting to ESP32 on {SERIAL_PORT}...")
    esp32 = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2) # Give ESP32 time to reboot upon connection
    print("Connection Established!\n")
except Exception as e:
    print(f"FAILED to connect. Is the USB plugged in? Error: {e}")
    sys.exit(1)

# --- BACKGROUND LISTENER THREAD ---
def listen_to_esp32():
    """Continuously reads incoming serial data without blocking user input."""
    while True:
        try:
            if esp32.in_waiting:
                incoming_data = esp32.readline().decode('utf-8').strip()
                
                # Intercept Battery Voltage updates
                if incoming_data.startswith("<V,") and incoming_data.endswith(">"):
                    clean_data = incoming_data.strip("<>")
                    parts = clean_data.split(",")
                    voltage = float(parts[1])
                    
                    # Print over the current line cleanly
                    sys.stdout.write(f"\r[SYS] Battery Health: {voltage:.2f}V   \nCommand > ")
                    sys.stdout.flush()
                
                # Print standard ESP32 replies (ACKs)
                elif incoming_data:
                    sys.stdout.write(f"\r[ESP32] {incoming_data}\nCommand > ")
                    sys.stdout.flush()
        except:
            # Exit thread quietly if serial port closes
            break

# Start the background listener
listener_thread = threading.Thread(target=listen_to_esp32, daemon=True)
listener_thread.start()

# --- COMMAND TRANSMITTER ---
def send_command(cmd):
    formatted_cmd = f"<{cmd}>"
    esp32.write(formatted_cmd.encode('utf-8'))

print("=========================================")
print("     MANGROVE ROVER CONTROL CENTER       ")
print("=========================================")
print(" D,100,100   : Drive Forward")
print(" D,-100,-100 : Drive Reverse")
print(" D,100,-100  : Crab Walk / Pivot")
print(" P           : Drop Sapling (Plant)")
print(" S           : EMERGENCY STOP")
print(" Q           : Quit Program")
print("=========================================\n")

# --- MAIN CONTROL LOOP ---
try:
    while True:
        # Wait for user to type a command
        user_input = input("Command > ").strip().upper()
        
        if user_input == 'Q':
            print("\nShutting down... applying brakes.")
            send_command("S")
            time.sleep(0.5)
            break
            
        elif user_input:
            send_command(user_input)

except KeyboardInterrupt:
    # Safely handle Ctrl+C
    print("\nForce Quit detected. Applying brakes.")
    send_command("S")
    time.sleep(0.5)

finally:
    # Ensure port is closed cleanly
    esp32.close()
    print("Serial port closed. Rover Safe.")