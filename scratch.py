import RPi.GPIO as GPIO
import subprocess
import wave
import time
import os

# config
TRACK_PATH = "audio/numb_linkin-park.wav"
CLK_PIN = 17
DT_PIN = 27
CHUNK_SIZE = 512

print("Loading audio...")
try:
    wf = wave.open(TRACK_PATH, 'rb')
    CHANNELS = wf.getnchannels()
    SAMPLE_WIDTH = wf.getsampwidth()
    FRAMERATE = wf.getframerate()
    
    RAW_AUDIO_DATA = wf.readframes(wf.getnframes())
    wf.close()
    
    FRAME_SIZE = CHANNELS * SAMPLE_WIDTH
    TOTAL_FRAMES = len(RAW_AUDIO_DATA) // FRAME_SIZE
    print("song cached! total frames: {}".format(TOTAL_FRAMES))
except Exception as e:
    print("failed to load file {}".format(e))
    exit(1)

# open non-buffered ALSA audio pipe
print("Opening hardware audio link...")
aplay_process = subprocess.Popen(
    ['aplay', '-t', 'raw', '-c', str(CHANNELS), '-f', 'S16_LE', '-r', str(FRAMERATE), '--buffer-size=1024'],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)

# global state vars
playback_pointer = 0.0
wheel_velocity = 1.0  
last_encoder_time = time.time()
is_scratching = False

# hardware pins
GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(DT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def encoder_callback(channel):
    global wheel_velocity, last_encoder_time, is_scratching
    
    now = time.time()
    dt = now - last_encoder_time
    
    clk_state = GPIO.input(CLK_PIN)
    dt_state = GPIO.input(DT_PIN)
    
    if dt > 0.001:
        is_scratching = True
        speed = 0.02 / dt
        speed = min(speed, 3.5)  
        
        if clk_state != dt_state:
            wheel_velocity = speed      
        else:
            wheel_velocity = -speed     
            
        last_encoder_time = now

# connect interrupt handler
GPIO.add_event_detect(CLK_PIN, GPIO.BOTH, callback=encoder_callback, bouncetime=1)

print("\n~~~ GET DJ-ING! ~~~")

try:
    while playback_pointer < TOTAL_FRAMES - 1:
        now = time.time()
        
        # check if user has let go of wheel
        if now - last_encoder_time > 0.20:
            is_scratching = False
            wheel_velocity = 1.0

        # normal playback
        if not is_scratching:
            start_frame = int(playback_pointer)
            end_frame = start_frame + CHUNK_SIZE
            
            if end_frame >= TOTAL_FRAMES:
                break
                
            chunk_bytes = RAW_AUDIO_DATA[start_frame * FRAME_SIZE : end_frame * FRAME_SIZE]
            playback_pointer += CHUNK_SIZE

        # scratching
        else:
            # friction decay if paused mid scratch
            if now - last_encoder_time > 0.05:
                wheel_velocity *= 0.7
                if abs(wheel_velocity) < 0.1: 
                    wheel_velocity = 0.0

            chunk_bytes = bytearray()
            for i in range(CHUNK_SIZE):
                target_frame = int(playback_pointer + (i * wheel_velocity))
                
                if target_frame < 0:
                    target_frame = 0
                elif target_frame >= TOTAL_FRAMES:
                    target_frame = TOTAL_FRAMES - 1
                    
                start_byte = target_frame * FRAME_SIZE
                chunk_bytes.extend(RAW_AUDIO_DATA[start_byte : start_byte + FRAME_SIZE])
                
            playback_pointer += CHUNK_SIZE * wheel_velocity
            if playback_pointer < 0:
                playback_pointer = 0.0

        # shove data chunk out to sound card
        try:
            aplay_process.stdin.write(chunk_bytes)
        except BrokenPipeError:
            break
            
        # give CPU small rest to stay synced with audio time
        time.sleep(0.005)

except KeyboardInterrupt:
    print("\npowering down...")

finally:
    try:
        aplay_process.stdin.close()
        aplay_process.terminate()
    except:
        pass
    GPIO.cleanup()
    print("bye bye!")