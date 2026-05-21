#include <Arduino.h>

// ============================================================
// MANGROVE PLANTER - INTEGRATED ESP32 FIRMWARE
// Handles: Drive Motors, Revolver Planter, Planting Motor,
//          Linear Actuator
// Serial Protocol (from Raspberry Pi): <CMD,arg1,arg2,...>
//
// COMMAND REFERENCE:
//   <D,leftSpeed,rightSpeed>   — Drive motors (-245 to 245)
//   <P>                        — Execute full planting cycle
//   <S>                        — Emergency stop (all motors)
//   <R,cellCount>              — Index revolver N cells forward
//   <J,direction,dutyCycle>    — Manual JGY370 planting motor control
//   <A,direction,dutyCycle>    — Manual linear actuator control
//                                  direction: 1=extend, -1=retract, 0=stop
//   <H>                        — Home actuator (retract to home position)
//   <V?>                       — Force immediate voltage report
//
// LINEAR ACTUATOR:
//   Pin 4  — RPWM  — physically RETRACTS (wiring swapped)
//   Pin 5  — LPWM  — physically EXTENDS  (wiring swapped)
//   Internal limit switches stop travel at both ends automatically.
//   Travel time: 4000 ms each direction (limit switches are true stop).
//
// PLANTING SEQUENCE (triggered by <P>):
//   1. Stop drive motors
//   2. Revolver indexes one chamber forward — seedling ready at chute
//   3. Actuator EXTENDS — mangrove drops then it is sandwich by two rollers
//   4. JGY370 planting motor runs for 25 seconds
//      (actuator stays EXTENDED the entire time)
//   5. JGY370 motor stops
//   6. Actuator RETRACTS — clears path for rover
//   7. ACK sent to Pi — rover resumes driving immediately
//
// RESPONSES TO RASPBERRY PI:
//   ACK: ...                   — Command acknowledgment
//   <V,12.45>                  — Battery voltage report (every 2s)
//   [ERROR] ...                — Fault/timeout notifications
// ============================================================


// ============================================================
// SECTION 1: PIN DEFINITIONS
// ============================================================

// --- Drive Motors (HC-160A S2 Driver) ---
const int DIR_A1 = 17;
const int DIR_B1 = 21;
const int PWM_1  = 22;

const int DIR_A2 = 16;
const int DIR_B2 = 19;
const int PWM_2  = 18;

// --- Revolver Loader (JGB37-520 + HC-160A S2) ---
const uint8_t pinLoaderDirA = 25;
const uint8_t pinLoaderDirB = 26;
const uint8_t pinLoaderPWM  = 27;
const uint8_t pinLoaderEncA = 32;
const uint8_t pinLoaderEncB = 33;

// --- Spill Motor (JGY370 + HW-039) ---
const uint8_t pinJGY_RPWM = 23;
const uint8_t pinJGY_LPWM = 13;

// --- Linear Actuator (BTS7960 / IBT-2) ---
// NOTE: Motor wiring is physically swapped
//   RPWM (pin 4) = RETRACTS the actuator
//   LPWM (pin 5) = EXTENDS  the actuator
// All logic in setActuator() already accounts for this swap —
// pass direction 1 to extend and -1 to retract as normal.
// Internal limit switches handle end-stop protection.
const uint8_t pinActuator_RPWM = 4;   // physically retracts
const uint8_t pinActuator_LPWM = 5;   // physically extends

// --- Battery Voltage Sensor ---
const int VOLTAGE_PIN = 34;


// ============================================================
// SECTION 2: HARDWARE & MATH CONFIGURATION
// ============================================================

// ESP32 LEDC PWM Channels
//   Channel 0 — Revolver loader motor
//   Channel 1 — JGY370 spill motor forward
//   Channel 2 — JGY370 spill motor reverse
//   Channels 3-4 — Linear actuator (via analogWrite, auto-assigned)
//   Drive motors use analogWrite() (auto-assigned channels)
const uint32_t motorPWMFreq  = 20000;
const uint8_t  loaderPWMCh   = 0;
const uint8_t  jgyFwdPWMCh   = 1;
const uint8_t  jgyRevPWMCh   = 2;
const uint8_t  pwmResolution = 8;   // 8-bit = 0..255

// Revolver geometry
const float   motorCPR      = 8590.0;
const float   numberOfCells = 14.0;
const float   gearRatio     = 1.0;
const int32_t countsPerSlot = (int32_t)((motorCPR / numberOfCells) * gearRatio);

// P-controller gain for revolver indexing
const float Kp_revolver = 1.5;

// ── Linear actuator timing ────────────────────────────────────
// 4000 ms gives the actuator full travel time.
// Internal limit switches cut the motor at end-of-stroke so
// stalling against them for remaining ms is completely safe.
const uint32_t ACTUATOR_EXTEND_MS  = 4000;  // ms to fully extend
const uint32_t ACTUATOR_RETRACT_MS = 4000;  // ms to fully retract
const uint8_t  ACTUATOR_SPEED      = 255;   // Full power (0-255)

// ── Planting sequence timing ──────────────────────────────────
const uint32_t PLANT_MOTOR_MS   = 25000;  // ms JGY370 runs (25 s)
const uint32_t REVOLVER_TIMEOUT = 5000;   // jam detection timeout (ms)


// ============================================================
// SECTION 3: RUNTIME STATE VARIABLES
// ============================================================

volatile int32_t encoderCount   = 0;
int32_t          targetPosition = 0;
unsigned long    lastVoltageTime = 0;

const byte numChars = 64;
char receivedChars[numChars];
char tempChars[numChars];
bool newData = false;


// ============================================================
// SECTION 4: INTERRUPT SERVICE ROUTINE
// ============================================================

void IRAM_ATTR readEncoder() {
    if (digitalRead(pinLoaderEncB) == HIGH) encoderCount++;
    else                                    encoderCount--;
}


// ============================================================
// SECTION 5: MOTOR CONTROL FUNCTIONS
// ============================================================

void driveMotors(int leftSpeed, int rightSpeed) {
    leftSpeed  = constrain(leftSpeed,  -245, 245);
    rightSpeed = constrain(rightSpeed, -245, 245);

    if (leftSpeed > 0) {
        digitalWrite(DIR_A1, LOW);  digitalWrite(DIR_B1, HIGH);
        analogWrite(PWM_1, leftSpeed);
    } else if (leftSpeed < 0) {
        digitalWrite(DIR_A1, HIGH); digitalWrite(DIR_B1, LOW);
        analogWrite(PWM_1, abs(leftSpeed));
    } else {
        digitalWrite(DIR_A1, LOW);  digitalWrite(DIR_B1, LOW);
        analogWrite(PWM_1, 0);
    }

    if (rightSpeed > 0) {
        digitalWrite(DIR_A2, LOW);  digitalWrite(DIR_B2, HIGH);
        analogWrite(PWM_2, rightSpeed);
    } else if (rightSpeed < 0) {
        digitalWrite(DIR_A2, HIGH); digitalWrite(DIR_B2, LOW);
        analogWrite(PWM_2, abs(rightSpeed));
    } else {
        digitalWrite(DIR_A2, LOW);  digitalWrite(DIR_B2, LOW);
        analogWrite(PWM_2, 0);
    }
}

void setLoaderMotor(int8_t direction, uint8_t dutyCycle) {
    ledcWrite(loaderPWMCh, dutyCycle);
    if (direction == 1) {
        digitalWrite(pinLoaderDirA, HIGH);
        digitalWrite(pinLoaderDirB, LOW);
    } else if (direction == -1) {
        digitalWrite(pinLoaderDirA, LOW);
        digitalWrite(pinLoaderDirB, HIGH);
    } else {
        digitalWrite(pinLoaderDirA, LOW);
        digitalWrite(pinLoaderDirB, LOW);
    }
}

void setJGYMotor(int8_t direction, uint8_t dutyCycle) {
    if (direction == 1) {
        ledcWrite(jgyRevPWMCh, 0);
        ledcWrite(jgyFwdPWMCh, dutyCycle);
    } else if (direction == -1) {
        ledcWrite(jgyFwdPWMCh, 0);
        ledcWrite(jgyRevPWMCh, dutyCycle);
    } else {
        ledcWrite(jgyFwdPWMCh, 0);
        ledcWrite(jgyRevPWMCh, 0);
    }
}

/**
 * Control the linear actuator via BTS7960 (IBT-2).
 *
 * Motor wiring is physically swapped on this so the pin
 * assignments are inverted here to keep the API consistent:
 *   direction  1 = EXTEND  -> drives LPWM (pin 5)
 *   direction -1 = RETRACT -> drives RPWM (pin 4)
 *   direction  0 = STOP    -> both pins 0 (BTS7960 coasts)
 *
 * Both pins are zeroed before the active one is driven to
 * guarantee no shoot-through condition on the H-bridge.
 */
void setActuator(int8_t direction, uint8_t dutyCycle) {
    // Zero both first — never energise both simultaneously
    analogWrite(pinActuator_RPWM, 0);
    analogWrite(pinActuator_LPWM, 0);

    if (direction == 1) {
        // EXTEND — LPWM active (wiring swapped)
        analogWrite(pinActuator_LPWM, dutyCycle);
    } else if (direction == -1) {
        // RETRACT — RPWM active (wiring swapped)
        analogWrite(pinActuator_RPWM, dutyCycle);
    }
    // direction == 0: both stay 0 (coast stop)
}

// Fully extend — blocks for ACTUATOR_EXTEND_MS
// Limit switch inside actuator stops travel at end-of-stroke.
void actuatorExtend() {
    setActuator(-1, ACTUATOR_SPEED);
    delay(ACTUATOR_RETRACT_MS);
    setActuator(0, 0);
}

// Fully retract — blocks for ACTUATOR_RETRACT_MS
// Limit switch inside actuator stops travel at end-of-stroke.
void actuatorRetract() {
    setActuator(1, ACTUATOR_SPEED);
    delay(ACTUATOR_EXTEND_MS);
    setActuator(0, 0);
}

// Home: retract to fully closed / home position
void actuatorHome() {
    Serial.println("ACK: Actuator homing — retracting...");
    actuatorRetract();
    Serial.println("ACK: Actuator homed — fully retracted.");
}


// ============================================================
// SECTION 6: REVOLVER INDEXING
// ============================================================

void indexRevolver(int32_t target) {
    unsigned long startTime = millis();

    while (true) {
        int32_t error = target - encoderCount;

        if (error <= 5) {
            setLoaderMotor(0, 0);
            Serial.println("ACK: Revolver chamber aligned.");
            break;
        }

        if (millis() - startTime > REVOLVER_TIMEOUT) {
            setLoaderMotor(0, 0);
            Serial.println("[ERROR] Revolver jam or timeout detected! Halted.");
            break;
        }

        int speed = (int)(error * Kp_revolver);
        speed = constrain(speed, 40, 90);
        setLoaderMotor(1, (uint8_t)speed);
        delay(10);
    }
}


// ============================================================
// SECTION 7: PLANTING SEQUENCE
// ============================================================

/**
 * Full automated planting cycle — one seedling.
 *
 * ┌─────────────────────────────────────────────────────────┐
 * │  SEQUENCE                                               │
 * │                                                         │
 * │  Step 1 — Stop drive motors                             │
 * │                                                         │
 * │  Step 2 — Revolver indexes one chamber forward          │
 * │           Seedling is dropped                           │
 * │                                                         │
 * │  Step 3 — Actuator EXTENDS (4 s)                        │
 * │           Sandwich the mangrove by the two rollers      │
 * │                                                         │
 * │  Step 4 — JGY370 planting motor runs for 25 seconds     │
 * │           Actuator stays EXTENDED the entire time       │
 * │           Drives seedling down into the trench          │
 * │                                                         │
 * │  Step 5 — JGY370 motor stops                            │
 * │                                                         │
 * │  Step 6 — Actuator RETRACTS (4 s)                       │
 * │           Clears path so rover can drive forward        │
 * │                                                         │
 * │  Step 7 — ACK sent to Pi → rover drives forward         │
 * └─────────────────────────────────────────────────────────┘
 */
void executePlantSequence() {
    Serial.println("ACK: Planting sequence started.");

    // ── Step 1 — Halt locomotion ──────────────────────────────
    driveMotors(0, 0);
    Serial.println("  [1/6] Drive motors stopped.");

    // ── Step 2 — Index revolver (position seedling at chute) ─
    // Revolver rotates first so the seedling is staged and ready
    Serial.println("  [2/6] Indexing revolver — positioning seedling...");
    targetPosition += countsPerSlot;
    indexRevolver(targetPosition);

    // ── Step 3 — Extend actuator to sandwich mangrove ────────────────
    Serial.println("  [3/6] Actuator EXTEND — opening chute...");
    actuatorExtend();

    // ── Step 4 — Planting motor ON, actuator stays extended ──
    Serial.println("  [4/6] Planting motor ON — 25 seconds...");
    Serial.println("         (Actuator stays extended during planting)");
    setJGYMotor(1, 255);
    delay(PLANT_MOTOR_MS);

    // ── Step 5 — Planting motor OFF ───────────────────────────
    setJGYMotor(0, 0);
    Serial.println("  [5/6] Planting motor OFF.");

    // ── Step 6 — Retract actuator to clear path ───────────────
    Serial.println("  [6/6] Actuator RETRACT — clearing path for rover...");
    actuatorRetract();

    // ── Done — Pi receives this and immediately drives forward ─
    Serial.println("ACK: Planting sequence complete.");
}


// ============================================================
// SECTION 8: BATTERY VOLTAGE REPORTING
// ============================================================

void reportBatteryVoltage() {
    int   rawADC      = analogRead(VOLTAGE_PIN);
    float pinVoltage  = (rawADC / 4095.0f) * 3.3f;
    float battVoltage = pinVoltage * 5.51782699394f;
    Serial.print("<V,");
    Serial.print(battVoltage, 2);
    Serial.println(">");
}


// ============================================================
// SECTION 9: SERIAL PROTOCOL PARSER
// ============================================================

void recvWithStartEndMarkers() {
    static bool recvInProgress = false;
    static byte ndx = 0;
    const char startMarker = '<';
    const char endMarker   = '>';
    char rc;

    while (Serial.available() > 0 && !newData) {
        rc = Serial.read();
        if (recvInProgress) {
            if (rc != endMarker) {
                receivedChars[ndx] = rc;
                if (++ndx >= numChars) ndx = numChars - 1;
            } else {
                receivedChars[ndx] = '\0';
                recvInProgress = false;
                ndx = 0;
                newData = true;
            }
        } else if (rc == startMarker) {
            recvInProgress = true;
        }
    }
}

/**
 * Supported commands:
 *   D,left,right   — Tank drive
 *   P              — Full plant cycle
 *   S              — Emergency stop (all motors + actuator)
 *   R,cells        — Index revolver N cells
 *   J,dir,duty     — Manual JGY370 control
 *   A,dir,duty     — Manual actuator: 1=extend, -1=retract, 0=stop
 *   H              — Home actuator (retract to home position)
 *   V?             — Immediate voltage report
 */
void parseData() {
    char *token = strtok(tempChars, ",");
    if (token == nullptr) return;
    char command = token[0];

    if (command == 'D') {
        token = strtok(NULL, ",");  int leftSpeed  = token ? atoi(token) : 0;
        token = strtok(NULL, ",");  int rightSpeed = token ? atoi(token) : 0;
        driveMotors(leftSpeed, rightSpeed);
        Serial.println("ACK: Drive command executed.");

    } else if (command == 'P') {
        executePlantSequence();

    } else if (command == 'S') {
        driveMotors(0, 0);
        setLoaderMotor(0, 0);
        setJGYMotor(0, 0);
        setActuator(0, 0);
        Serial.println("ACK: Emergency stop — all motors halted.");

    } else if (command == 'R') {
        token = strtok(NULL, ",");
        int cells = token ? atoi(token) : 1;
        cells = max(cells, 1);
        Serial.printf("ACK: Indexing revolver %d cell(s)...\n", cells);
        targetPosition += countsPerSlot * cells;
        indexRevolver(targetPosition);

    } else if (command == 'J') {
        token = strtok(NULL, ",");  int8_t  dir  = token ? (int8_t)atoi(token)  : 0;
        token = strtok(NULL, ",");  uint8_t duty = token ? (uint8_t)atoi(token) : 0;
        setJGYMotor(dir, duty);
        Serial.printf("ACK: JGY370 set — dir=%d duty=%d\n", dir, duty);

    } else if (command == 'A') {
        token = strtok(NULL, ",");  int8_t  dir  = token ? (int8_t)atoi(token)  : 0;
        token = strtok(NULL, ",");  uint8_t duty = token ? (uint8_t)atoi(token) : ACTUATOR_SPEED;
        setActuator(dir, duty);
        const char* dirStr = (dir == 1) ? "EXTEND" : (dir == -1) ? "RETRACT" : "STOP";
        Serial.printf("ACK: Actuator %s — duty=%d\n", dirStr, duty);

    } else if (command == 'H') {
        actuatorHome();

    } else if (command == 'V') {
        reportBatteryVoltage();

    } else {
        Serial.printf("[WARN] Unknown command: '%c'\n", command);
    }
}


// ============================================================
// SECTION 10: SETUP
// ============================================================

void setup() {
    Serial.begin(115200);

    // --- Drive motor pins ---
    pinMode(DIR_A1, OUTPUT); pinMode(DIR_B1, OUTPUT); pinMode(PWM_1, OUTPUT);
    pinMode(DIR_A2, OUTPUT); pinMode(DIR_B2, OUTPUT); pinMode(PWM_2, OUTPUT);
    driveMotors(0, 0);

    // --- Revolver loader motor ---
    pinMode(pinLoaderDirA, OUTPUT);
    pinMode(pinLoaderDirB, OUTPUT);
    ledcSetup(loaderPWMCh, motorPWMFreq, pwmResolution);
    ledcAttachPin(pinLoaderPWM, loaderPWMCh);
    setLoaderMotor(0, 0);

    // --- JGY370 spill motor ---
    ledcSetup(jgyFwdPWMCh, motorPWMFreq, pwmResolution);
    ledcAttachPin(pinJGY_RPWM, jgyFwdPWMCh);
    ledcSetup(jgyRevPWMCh, motorPWMFreq, pwmResolution);
    ledcAttachPin(pinJGY_LPWM, jgyRevPWMCh);
    setJGYMotor(0, 0);

    // --- Linear actuator (BTS7960) ---
    // pinMode only — analogWrite() handles LEDC channel assignment
    // automatically on first call.
    pinMode(pinActuator_RPWM, OUTPUT);
    pinMode(pinActuator_LPWM, OUTPUT);
    digitalWrite(pinActuator_RPWM, LOW);
    digitalWrite(pinActuator_LPWM, LOW);

    // --- Encoder ---
    pinMode(pinLoaderEncA, INPUT_PULLUP);
    pinMode(pinLoaderEncB, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(pinLoaderEncA), readEncoder, RISING);

    // --- Startup: home actuator to fully retracted position ---
    Serial.println("[INIT] Homing actuator — retracting to home position...");
    actuatorRetract();
    Serial.println("[INIT] Actuator ready — fully retracted.");

    Serial.println("=== Mangrove Planter ESP32 Ready ===");
    Serial.printf("  Counts per revolver slot : %d\n", countsPerSlot);
    Serial.printf("  Actuator travel time     : %d ms each direction\n", ACTUATOR_EXTEND_MS);
    Serial.println("  Awaiting commands from Raspberry Pi...");
}


// ============================================================
// SECTION 11: MAIN LOOP
// ============================================================

void loop() {
    recvWithStartEndMarkers();
    if (newData) {
        strcpy(tempChars, receivedChars);
        parseData();
        newData = false;
    }

    if (millis() - lastVoltageTime > 2000) {
        reportBatteryVoltage();
        lastVoltageTime = millis();
    }
}