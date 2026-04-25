#include <ESP32Servo.h>

// --- PIN DEFINITIONS FOR HC-160A S2 ---
// Left Motor (Motor A)
const int DIR_A1 = 16; // Pin A
const int DIR_B1 = 19; // Pin B
const int PWM_1 = 18;  // Pin PA

// Right Motor (Motor a)
const int DIR_A2 = 17; // Pin a
const int DIR_B2 = 21; // Pin b
const int PWM_2 = 22;  // Pin Pb

// Servos / Planter
//Servo saplingDoorServo;
//Servo planterMouthServo;
//const int SAPLING_DOOR_PIN = 4;
//const int PLANTER_MOUTH_PIN = 16;
//const int LINEAR_PLUNGE_RELAY = 17; 

// --- SERIAL PARSING VARIABLES ---
const byte numChars = 32;
char receivedChars[numChars];
char tempChars[numChars];
boolean newData = false;

void setup() {
  Serial.begin(115200);
  
  // Setup Motor Pins
  pinMode(DIR_A1, OUTPUT); pinMode(DIR_B1, OUTPUT); pinMode(PWM_1, OUTPUT);
  pinMode(DIR_A2, OUTPUT); pinMode(DIR_B2, OUTPUT); pinMode(PWM_2, OUTPUT);
  
  // Ensure motors start totally stopped (Brake Mode)
  digitalWrite(DIR_A1, LOW); digitalWrite(DIR_B1, LOW); analogWrite(PWM_1, 0);
  digitalWrite(DIR_A2, LOW); digitalWrite(DIR_B2, LOW); analogWrite(PWM_2, 0);
  
  // Setup Planter Pins
  pinMode(LINEAR_PLUNGE_RELAY, OUTPUT);
  saplingDoorServo.attach(SAPLING_DOOR_PIN);
  planterMouthServo.attach(PLANTER_MOUTH_PIN);
  
  // Set initial positions
  saplingDoorServo.write(0);
  planterMouthServo.write(0);
  digitalWrite(LINEAR_PLUNGE_RELAY, LOW);
  
  Serial.println("ESP32 Muscle Layer Ready (HC-160A S2 Mode).");
}

void loop() {
  recvWithStartEndMarkers();
  if (newData == true) {
    strcpy(tempChars, receivedChars);
    parseData();
    newData = false;
  }
}

// --- COMMUNICATION LOGIC ---
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

void parseData() {
  char * strtokIndx; 
  
  strtokIndx = strtok(tempChars, ","); // Get the command letter
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
    // Parse Plant Sequence
    Serial.println("ACK: Planting Sequence Initiated");
    plantSequence();
    Serial.println("ACK: Planting Sequence Complete");
    
  } else if (command == 'S') {
    driveMotors(0, 0);
    Serial.println("ACK: Emergency Stop");
  }
}

// --- HARDWARE CONTROL LOGIC (UPDATED FOR HC-160A S2) ---
void driveMotors(int leftSpeed, int rightSpeed) {
  // Constrain limits
  leftSpeed = constrain(leftSpeed, -255, 255);
  rightSpeed = constrain(rightSpeed, -255, 255);

  // Left Motor Control (Motor A)
  if (leftSpeed > 0) {
    digitalWrite(DIR_A1, HIGH);
    digitalWrite(DIR_B1, LOW);
    analogWrite(PWM_1, leftSpeed);
  } else if (leftSpeed < 0) {
    digitalWrite(DIR_A1, LOW);
    digitalWrite(DIR_B1, HIGH);
    analogWrite(PWM_1, abs(leftSpeed));
  } else {
    // Both LOW = Active Braking
    digitalWrite(DIR_A1, LOW);
    digitalWrite(DIR_B1, LOW);
    analogWrite(PWM_1, 0);
  }

  // Right Motor Control (Motor a)
  if (rightSpeed > 0) {
    digitalWrite(DIR_A2, HIGH);
    digitalWrite(DIR_B2, LOW);
    analogWrite(PWM_2, rightSpeed);
  } else if (rightSpeed < 0) {
    digitalWrite(DIR_A2, LOW);
    digitalWrite(DIR_B2, HIGH);
    analogWrite(PWM_2, abs(rightSpeed));
  } else {
    // Both LOW = Active Braking
    digitalWrite(DIR_A2, LOW);
    digitalWrite(DIR_B2, LOW);
    analogWrite(PWM_2, 0);
  }
}