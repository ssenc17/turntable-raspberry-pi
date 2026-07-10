import RPi.GPIO as GPIO
import time

CLK_PIN = 17
DT_PIN = 27

GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(DT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

counter = 0
clk_last_state = GPIO.input(CLK_PIN)

print("ready to spin!")

try:
    while True:
        clk_state = GPIO.input(CLK_PIN)
        dt_state = GPIO.input(DT_PIN)
        
        if clk_state != clk_last_state:
            if dt_state != clk_state:
                counter += 1
            else:
                counter -= 1
            print("wheel moved! position: " + str(counter))
            
        clk_last_state = clk_state
        time.sleep(0.001)

except KeyboardInterrupt:
    GPIO.cleanup()