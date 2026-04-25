import serial
import time

# Connect to the ESP32
serial_port = '/dev/ttyUSB0' # CHANGE THIS to /dev/ttyACM0 if necessary
baud_rate = 115200

try:
    print(f"Connecting to ESP32 on {serial_port}...")
    esp32 = serial.Serial(serial_port, baud_rate, timeout=1)
    time.sleep(2) # Wait 2 seconds for ESP32 to reset upon connection
    print("Connection Established!")
except Exception as e:
    print(f"FAILED to connect: {e}")
    exit()

def send_command(cmd):
    # Wrap the command in our start/end markers
    formatted_cmd = f"<{cmd}>"
    esp32.write(formatted_cmd.encode('utf-8'))
    print(f"Sent: {formatted_cmd}")
    
    # Read any acknowledgement back from the ESP32
    time.sleep(0.1)
    while esp32.in_waiting:
        print("ESP32 Reply:", esp32.readline().decode('utf-8').strip())

print("\n--- MANGROVE ROVER MANUAL CONTROL ---")
print("Command Guide:")
print("  D,100,100   (Drive Forward slowly)")
print("  D,-100,-100 (Drive Reverse slowly)")
print("  D,100,-100  (Crab Walk / Pivot)")
print("  S           (Emergency Brake)")
print("  Q           (Quit Program)\n")

try:
    while True:
        user_input = input("Enter Command: ").strip().upper()
        
        if user_input == 'Q':
            print("Shutting down... applying brakes.")
            send_command("S")
            break
            
        send_command(user_input)

except KeyboardInterrupt:
    # If you press Ctrl+C, forcefully stop the motors
    print("\nForce Quit detected. Applying brakes.")
    send_command("S")

finally:
    esp32.close()
    print("Serial port closed safely.")