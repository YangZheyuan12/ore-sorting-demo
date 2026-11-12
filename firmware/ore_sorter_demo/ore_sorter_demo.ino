#include <Servo.h>

// ---------- 按实际接线修改 ----------
const uint8_t ENTRY_SENSOR_PIN = 2;   // 5V NPN，对射遮挡时 LOW
const uint8_t EXIT_SENSOR_PIN = 3;
const uint8_t ESTOP_PIN = 4;          // 急停辅助触点，触发时 LOW
const uint8_t MOTOR_RELAY_PIN = 7;
const uint8_t SERVO_PIN = 9;
const uint8_t MOTOR_ON_LEVEL = HIGH;
const uint8_t MOTOR_OFF_LEVEL = LOW;

// ---------- 机械标定参数 ----------
const int ORE_ANGLE = 90;             // 默认目标矿 A
const int WASTE_ANGLE = 115;          // 向废石 B 偏转约 25°
const uint32_t DEBOUNCE_MS = 25;
const uint32_t RECOVERY_MS = 200;
const uint32_t SORT_DELAY_MS = 550;   // d/v - t_servo - t_margin 的示例标定值
const uint32_t SERVO_TRAVEL_MS = 250;
const uint32_t SERVO_HOLD_MS = 500;
const uint32_t POST_BATCH_RUN_MS = 1800;
const uint8_t QUEUE_CAPACITY = 32;

enum Label : uint8_t { LABEL_ORE, LABEL_WASTE };
enum RunState : uint8_t { STATE_IDLE, STATE_READY, STATE_RUNNING, STATE_PAUSED, STATE_ERROR, STATE_ESTOP };
enum ActuatorState : uint8_t { ACT_IDLE, ACT_WAIT, ACT_HOLD };

struct QueueItem {
  String id;
  Label label;
};

struct SensorFilter {
  bool rawState = HIGH;
  bool stableState = HIGH;
  bool armed = true;
  uint32_t rawChangedAt = 0;
  uint32_t clearSince = 0;

  void begin(uint8_t pin) {
    rawState = digitalRead(pin);
    stableState = rawState;
    armed = (stableState == HIGH);
    rawChangedAt = millis();
    clearSince = millis();
  }

  bool update(uint8_t pin, uint32_t now) {
    bool current = digitalRead(pin);
    if (current != rawState) {
      rawState = current;
      rawChangedAt = now;
    }
    if (rawState != stableState && now - rawChangedAt >= DEBOUNCE_MS) {
      stableState = rawState;
      if (stableState == LOW && armed) {
        armed = false;
        return true;  // 稳定下降沿：光束被矿石遮挡
      }
      if (stableState == HIGH) {
        clearSince = now;
      }
    }
    if (!armed && stableState == HIGH && now - clearSince >= RECOVERY_MS) {
      armed = true;
    }
    return false;
  }
};

Servo chuteServo;
SensorFilter entrySensor;
SensorFilter exitSensor;
QueueItem queueItems[QUEUE_CAPACITY];
uint8_t queueHead = 0;
uint8_t queueTail = 0;
uint8_t queueSize = 0;
uint8_t expectedBatch = 0;
uint8_t inCount = 0;
uint8_t outCount = 0;
uint8_t oreCount = 0;
uint8_t wasteCount = 0;
RunState runState = STATE_IDLE;
ActuatorState actuatorState = ACT_IDLE;
uint32_t actuatorAt = 0;
bool batchStopPending = false;
uint32_t batchStopAt = 0;
String serialLine;

const char* labelName(Label label) {
  return label == LABEL_ORE ? "ORE" : "WASTE";
}

const char* stateName() {
  switch (runState) {
    case STATE_IDLE: return "IDLE";
    case STATE_READY: return "READY";
    case STATE_RUNNING: return "RUNNING";
    case STATE_PAUSED: return "PAUSED";
    case STATE_ERROR: return "ERROR";
    case STATE_ESTOP: return "ESTOP";
  }
  return "UNKNOWN";
}

void motorSet(bool enabled) {
  digitalWrite(MOTOR_RELAY_PIN, enabled ? MOTOR_ON_LEVEL : MOTOR_OFF_LEVEL);
}

void servoDefault() {
  chuteServo.write(ORE_ANGLE);
  actuatorState = ACT_IDLE;
}

void clearQueue() {
  queueHead = queueTail = queueSize = 0;
}

bool queuePush(const String& id, Label label) {
  if (queueSize >= QUEUE_CAPACITY) return false;
  queueItems[queueTail].id = id;
  queueItems[queueTail].label = label;
  queueTail = (queueTail + 1) % QUEUE_CAPACITY;
  queueSize++;
  return true;
}

bool queuePop(QueueItem& item) {
  if (queueSize == 0) return false;
  item = queueItems[queueHead];
  queueHead = (queueHead + 1) % QUEUE_CAPACITY;
  queueSize--;
  return true;
}

void resetMachine() {
  motorSet(false);
  servoDefault();
  clearQueue();
  expectedBatch = inCount = outCount = oreCount = wasteCount = 0;
  batchStopPending = false;
  runState = STATE_IDLE;
  Serial.println("READY");
}

void failMachine(const char* code) {
  motorSet(false);
  servoDefault();
  batchStopPending = false;
  runState = STATE_ERROR;
  Serial.print("ERROR ");
  Serial.println(code);
}

void handleEntry(uint32_t now) {
  (void)now;
  if (runState != STATE_RUNNING) return;
  inCount++;
  Serial.print("EVENT ENTRY ");
  Serial.println(inCount);
  if (inCount > expectedBatch) {
    failMachine("EXTRA_OBJECT");
  }
}

void scheduleWasteActuation(uint32_t now) {
  if (actuatorState != ACT_IDLE) {
    failMachine("ACTUATOR_BUSY");
    return;
  }
  actuatorState = ACT_WAIT;
  actuatorAt = now + SORT_DELAY_MS;
}

void handleExit(uint32_t now) {
  if (runState != STATE_RUNNING) return;
  if (queueSize == 0) {
    failMachine("QUEUE_EMPTY");
    return;
  }
  if (outCount >= inCount) {
    failMachine("SEQUENCE_ERROR");
    return;
  }

  QueueItem item;
  queuePop(item);
  outCount++;
  if (item.label == LABEL_WASTE) {
    wasteCount++;
    scheduleWasteActuation(now);
  } else {
    oreCount++;
  }

  Serial.print("EVENT EXIT ");
  Serial.print(item.id);
  Serial.print(' ');
  Serial.println(labelName(item.label));

  if (outCount == expectedBatch && queueSize == 0) {
    batchStopPending = true;
    batchStopAt = now + POST_BATCH_RUN_MS;
    Serial.print("DONE ");
    Serial.print(oreCount);
    Serial.print(' ');
    Serial.println(wasteCount);
  }
}

void updateActuator(uint32_t now) {
  if (actuatorState == ACT_WAIT && (int32_t)(now - actuatorAt) >= 0) {
    chuteServo.write(WASTE_ANGLE);
    actuatorState = ACT_HOLD;
    actuatorAt = now + SERVO_TRAVEL_MS + SERVO_HOLD_MS;
    Serial.println("STATE SERVO_WASTE");
  } else if (actuatorState == ACT_HOLD && (int32_t)(now - actuatorAt) >= 0) {
    servoDefault();
    Serial.println("STATE SERVO_ORE");
  }
}

String tokenAt(const String& line, uint8_t index) {
  int start = 0;
  for (uint8_t i = 0; i < index; i++) {
    start = line.indexOf(' ', start);
    if (start < 0) return "";
    start++;
  }
  int end = line.indexOf(' ', start);
  if (end < 0) end = line.length();
  return line.substring(start, end);
}

void handleCommand(String line) {
  line.trim();
  if (!line.length()) return;
  String command = tokenAt(line, 0);
  command.toUpperCase();

  if (command == "RESET") {
    resetMachine();
  } else if (command == "BATCH") {
    if (runState == STATE_RUNNING) {
      Serial.println("ERROR BUSY");
      return;
    }
    int count = tokenAt(line, 1).toInt();
    if (count <= 0 || count > QUEUE_CAPACITY) {
      Serial.println("ERROR BAD_BATCH_SIZE");
      return;
    }
    clearQueue();
    expectedBatch = count;
    inCount = outCount = oreCount = wasteCount = 0;
    runState = STATE_READY;
    Serial.print("ACK BATCH ");
    Serial.println(expectedBatch);
  } else if (command == "PUSH") {
    if (runState != STATE_READY) {
      Serial.println("ERROR NOT_READY");
      return;
    }
    String id = tokenAt(line, 1);
    String labelText = tokenAt(line, 2);
    labelText.toUpperCase();
    if (!id.length() || (labelText != "ORE" && labelText != "WASTE")) {
      Serial.println("ERROR BAD_PUSH");
      return;
    }
    if (!queuePush(id, labelText == "WASTE" ? LABEL_WASTE : LABEL_ORE)) {
      Serial.println("ERROR QUEUE_FULL");
      return;
    }
    Serial.print("ACK PUSH ");
    Serial.println(id);
  } else if (command == "START") {
    if (runState != STATE_READY || queueSize != expectedBatch || expectedBatch == 0) {
      Serial.println("ERROR BATCH_NOT_READY");
      return;
    }
    runState = STATE_RUNNING;
    motorSet(true);
    Serial.println("STATE RUNNING");
  } else if (command == "STOP") {
    motorSet(false);
    servoDefault();
    if (runState == STATE_RUNNING || runState == STATE_PAUSED) runState = STATE_READY;
    Serial.println("STATE STOPPED");
  } else if (command == "PAUSE") {
    if (runState != STATE_RUNNING) {
      Serial.println("ERROR NOT_RUNNING");
      return;
    }
    motorSet(false);
    runState = STATE_PAUSED;
    Serial.println("STATE PAUSED");
  } else if (command == "RESUME") {
    if (runState != STATE_PAUSED) {
      Serial.println("ERROR NOT_PAUSED");
      return;
    }
    runState = STATE_RUNNING;
    motorSet(true);
    Serial.println("STATE RUNNING");
  } else if (command == "ESTOP") {
    motorSet(false);
    servoDefault();
    runState = STATE_ESTOP;
    Serial.println("ERROR ESTOP");
  } else if (command == "PING") {
    Serial.println("PONG");
  } else if (command == "STATUS") {
    Serial.print("INFO ");
    Serial.print(stateName());
    Serial.print(" IN=");
    Serial.print(inCount);
    Serial.print(" OUT=");
    Serial.print(outCount);
    Serial.print(" Q=");
    Serial.println(queueSize);
  } else {
    Serial.println("ERROR UNKNOWN_COMMAND");
  }
}

void pollSerial() {
  while (Serial.available()) {
    char value = (char)Serial.read();
    if (value == '\n') {
      handleCommand(serialLine);
      serialLine = "";
    } else if (value != '\r' && serialLine.length() < 96) {
      serialLine += value;
    }
  }
}

void setup() {
  pinMode(ENTRY_SENSOR_PIN, INPUT_PULLUP);
  pinMode(EXIT_SENSOR_PIN, INPUT_PULLUP);
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  pinMode(MOTOR_RELAY_PIN, OUTPUT);
  motorSet(false);
  chuteServo.attach(SERVO_PIN);
  servoDefault();
  Serial.begin(115200);
  entrySensor.begin(ENTRY_SENSOR_PIN);
  exitSensor.begin(EXIT_SENSOR_PIN);
  delay(50);  // 仅上电稳定；运行期间全程非阻塞
  Serial.println("READY");
}

void loop() {
  uint32_t now = millis();
  pollSerial();

  if (digitalRead(ESTOP_PIN) == LOW && runState != STATE_ESTOP) {
    motorSet(false);
    servoDefault();
    runState = STATE_ESTOP;
    Serial.println("ERROR ESTOP");
  }

  if (runState == STATE_RUNNING) {
    if (entrySensor.update(ENTRY_SENSOR_PIN, now)) handleEntry(now);
    if (exitSensor.update(EXIT_SENSOR_PIN, now)) handleExit(now);
  } else {
    entrySensor.update(ENTRY_SENSOR_PIN, now);
    exitSensor.update(EXIT_SENSOR_PIN, now);
  }

  updateActuator(now);
  if (batchStopPending && (int32_t)(now - batchStopAt) >= 0) {
    motorSet(false);
    batchStopPending = false;
    runState = STATE_READY;
    Serial.println("STATE BATCH_COMPLETE");
  }
}
