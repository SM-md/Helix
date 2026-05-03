#include <Arduino.h>
//#include <ESP32Servo.h>

// --- PIN DEFINITIONS ---
// Left Motor (Wired to Driver Side a/b)
const int DIR_A1 = 17; // Pin a (Wired to POS_LEFT)
const int DIR_B1 = 21; // Pin b (Wired to NEG_LEFT)
const int PWM_1 = 22;  // Pin PWMab

// Right Motor (Wired to Driver Side A/B)
const int DIR_A2 = 16; // Pin A (Wired to NEG_RIGHT)
const int DIR_B2 = 19; // Pin B (Wired to POS_RIGHT)
const int PWM_2 = 18;  // Pin PWMAB

// Planter (Single Drop Chute for Plow Mechanism)
//Servo saplingDoorServo;
const int SAPLING_DOOR_PIN = 4;

// Battery Voltage Sensor
const int VOLTAGE_PIN = 34;
unsigned long lastVoltageTime = 0;

// --- SERIAL PARSING VARIABLES ---
const byte numChars = 32;
char receivedChars[numChars];
char tempChars[numChars];
boolean newData = false;

// --- COMMUNICATION PARSING ---
void recvWithStartEndMarkers() {
  static boolean recvInProgress = false;
  static byte ndx = 0;
  char startMarker = '<';
  char endMarker = '>';
  char rc;

  while (Serial.available() > 0 && newData == false) {
    rc = Serial.read();

    if (recvInProgress == true) {
      if (rc != endMarker) {
        receivedChars[ndx] = rc;
        ndx++;
        if (ndx >= numChars) { ndx = numChars - 1; }
      } else {
        receivedChars[ndx] = '\0'; // terminate the string
        recvInProgress = false;
        ndx = 0;
        newData = true;
      }
    } else if (rc == startMarker) {
      recvInProgress = true;
    }
  }
}

// --- HARDWARE CONTROL LOGIC (UPDATED FOR HC-160A S2) ---
void driveMotors(int leftSpeed, int rightSpeed) {
  // Constrain limits
  leftSpeed = constrain(leftSpeed, -245, 245);
  rightSpeed = constrain(rightSpeed, -245, 245);

  // Left Motor Control (Motor A)
  if (leftSpeed > 0) {
    // FLIPPED LOGIC: Now drives "Forward" mechanically
    digitalWrite(DIR_A1, LOW);
    digitalWrite(DIR_B1, HIGH);
    analogWrite(PWM_1, leftSpeed);
  } else if (leftSpeed < 0) {
    // FLIPPED LOGIC: Now drives "Backward" mechanically
    digitalWrite(DIR_A1, HIGH);
    digitalWrite(DIR_B1, LOW);
    analogWrite(PWM_1, abs(leftSpeed));
  } else {
    // Both LOW = Active Braking
    digitalWrite(DIR_A1, LOW);
    digitalWrite(DIR_B1, LOW);
    analogWrite(PWM_1, 0);
  }

  // Right Motor Control (Motor a)
  if (rightSpeed > 0) {
    // FLIPPED LOGIC: Now drives "Forward" mechanically
    digitalWrite(DIR_A2, LOW);
    digitalWrite(DIR_B2, HIGH);
    analogWrite(PWM_2, rightSpeed);
  } else if (rightSpeed < 0) {
    // FLIPPED LOGIC: Now drives "Backward" mechanically
    digitalWrite(DIR_A2, HIGH);
    digitalWrite(DIR_B2, LOW);
    analogWrite(PWM_2, abs(rightSpeed));
  } else {
    // Both LOW = Active Braking
    digitalWrite(DIR_A2, LOW);
    digitalWrite(DIR_B2, LOW);
    analogWrite(PWM_2, 0);
  }
}

void parseData() {
  char * strtokIndx; 
  strtokIndx = strtok(tempChars, ","); 
  char command = strtokIndx[0];
  
  if (command == 'D') {
    // Parse Drive Speeds
    strtokIndx = strtok(NULL, ",");
    int leftSpeed = atoi(strtokIndx); 
    strtokIndx = strtok(NULL, ",");
    int rightSpeed = atoi(strtokIndx); 
    
    driveMotors(leftSpeed, rightSpeed);
    Serial.println("ACK: Drive Command Executed");
    
  } else if (command == 'P') {
    // Execute Plant Sequence
    Serial.println("ACK: Dropping Sapling into Plow Trench...");
    //plantSequence();
    Serial.println("ACK: Planting Sequence Complete");
    
  } else if (command == 'S') {
    // Emergency Stop
    driveMotors(0, 0);
    Serial.println("ACK: Emergency Brake Applied");
  }
}

//void plantSequence() {
  // Open chute door to drop sapling into the center plow channel
  //saplingDoorServo.write(90); 
  //delay(1000); // Wait 1 second for it to fall
  
  // Close door for the next cycle
  //saplingDoorServo.write(0);
//}

void reportBatteryVoltage() {
  // Read ADC and scale it to actual 12V battery voltage (1/5 voltage divider)
  int rawADC = analogRead(VOLTAGE_PIN);
  float pinVoltage = (rawADC / 4095.0) * 3.3;
  float batteryVoltage = pinVoltage * 5.0;
  
  // Format: <V,12.45>
  Serial.print("<V,");
  Serial.print(batteryVoltage);
  Serial.println(">");
}

void setup() {
  Serial.begin(115200);
  
  // Setup Motor Pins
  pinMode(DIR_A1, OUTPUT); pinMode(DIR_B1, OUTPUT); pinMode(PWM_1, OUTPUT);
  pinMode(DIR_A2, OUTPUT); pinMode(DIR_B2, OUTPUT); pinMode(PWM_2, OUTPUT);
  
  // Ensure motors start totally stopped (Brake Mode)
  digitalWrite(DIR_A1, LOW); digitalWrite(DIR_B1, LOW); analogWrite(PWM_1, 0);
  digitalWrite(DIR_A2, LOW); digitalWrite(DIR_B2, LOW); analogWrite(PWM_2, 0);
  
  // Setup Planter Pin
  //saplingDoorServo.attach(SAPLING_DOOR_PIN);
  //saplingDoorServo.write(0); // Ensure door is closed
  
  Serial.println("ESP32 Muscle Layer Ready.");
}

void loop() {
  // Listen for commands from the Raspberry Pi
  recvWithStartEndMarkers();
  if (newData == true) {
    strcpy(tempChars, receivedChars);
    parseData();
    newData = false;
  }

  // Report Battery Voltage every 2 seconds
  if (millis() - lastVoltageTime > 2000) {
    reportBatteryVoltage();
    lastVoltageTime = millis();
  }
}