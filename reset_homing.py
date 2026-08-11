"""Reset Homing_Offset to 0 on all follower arm motors before re-calibration.

Run BEFORE lerobot_calibrate when you get:
  ValueError: Magnitude XXXX exceeds 2047 (max for sign_bit_index=11)
"""

import sys
from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech.feetech import FeetechMotorsBus

PORT = "COM3"
# Stejná konstrukce jako v so_follower.py — FeetechMotorsBus čeká objekty
# Motor(id, model, norm_mode), ne holé tuply (odtud AttributeError: 'tuple'
# object has no attribute 'id').
MOTORS = {
    "shoulder_pan":  Motor(1, "sts3215", MotorNormMode.DEGREES),
    "shoulder_lift": Motor(2, "sts3215", MotorNormMode.DEGREES),
    "elbow_flex":    Motor(3, "sts3215", MotorNormMode.DEGREES),
    "wrist_flex":    Motor(4, "sts3215", MotorNormMode.DEGREES),
    "wrist_roll":    Motor(5, "sts3215", MotorNormMode.DEGREES),
    "gripper":       Motor(6, "sts3215", MotorNormMode.RANGE_0_100),
}

def main():
    bus = FeetechMotorsBus(port=PORT, motors=MOTORS)
    bus.connect()
    print(f"Pripojeno k {PORT}. Resetuji Homing_Offset na 0...")
    for name, motor in MOTORS.items():
        try:
            bus.write("Homing_Offset", name, 0)
            pos = bus.read("Present_Position", name)
            print(f"  {name:20s} id={motor.id}  offset=0  pos={pos}")
        except Exception as e:
            print(f"  {name:20s} CHYBA: {e}", file=sys.stderr)
    bus.disconnect()
    print("\nHotovo - ted spust kalibraci znovu.")

if __name__ == "__main__":
    main()
