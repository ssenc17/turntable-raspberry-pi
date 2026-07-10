import RPi.GPIO as GPIO
import subprocess
import wave
import time
import os
import threading

# config
PLAYLIST = [
    "audio/bring-me-to-life_evanescence.wav",
    "audio/numb_linkin-park.wav",
    "audio/clarity_zedd.wav",
]

current_track_index = 0

CLK_PIN = 17
DT_PIN = 27
BUTTON_PIN = 22
STEPS_PER_360 = 24  
CROSSFADE_TIME = 2.5

# hardware tracker states
cumulative_steps = 0
last_step_time = time.time()
is_paused = False
last_button_press = time.time()

# audio state management
playback_pointer = 0.0
wheel_velocity = 1.0  
last_encoder_time = time.time()
is_scratching = False
track_change_triggered = False

audio_lock = threading.Lock()

class AudioDeck:
    def __init__(self):
        self.raw_data = None
        self.frame_size = 0
        self.total_frames = 0
        self.channels = 2
        self.sample_width = 2
        self.framerate = 44100
        self.volume = 1.0
        self.process = None

    def load_file(self, path):
        try:
            wf = wave.open(path, 'rb')
            self.channels = wf.getnchannels()
            self.sample_width = wf.getsampwidth()
            self.framerate = wf.getframerate()
            self.raw_data = wf.readframes(wf.getnframes())
            wf.close()
            self.frame_size = self.channels * self.sample_width
            self.total_frames = len(self.raw_data) // self.frame_size
            return True
        
        except Exception as e:
            print("error loading {}: {}".format(path, e))
            return False

    def start_process(self):
        # open non-buffered ALSA audio pipe
        self.process = subprocess.Popen(
            ['aplay', '-t', 'raw', '-c', str(self.channels), '-f', 'S16_LE', '-r', str(self.framerate), '--buffer-size=1024'],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
            except:
                pass

# initialize two decks
deck_active = AudioDeck()
deck_fading = AudioDeck()

# Load initial song
deck_active.load_file(PLAYLIST[current_track_index])
deck_active.start_process()

def apply_volume_to_chunk(raw_bytes, volume):
    if volume >= 1.0:
        return raw_bytes

    import struct
    fmt = "<{}h".format(len(raw_bytes) // 2)
    try:
        shorts = list(struct.unpack(fmt, raw_bytes))
        for i in range(len(shorts)):
            shorts[i] = int(shorts[i] * volume)
        return struct.pack(fmt, *shorts)
    except:
        return raw_bytes

def crossfade_worker(old_deck, new_track_path):
    global deck_active, deck_fading, playback_pointer, track_change_triggered, cumulative_steps
    
    # prepare deck B in background memory arrays
    deck_fading.load_file(new_track_path)
    deck_fading.volume = 0.0
    deck_fading.start_process()
    
    steps = 25
    sleep_interval = CROSSFADE_TIME / steps
    
    for i in range(steps):
        time.sleep(sleep_interval)
        with audio_lock:
            # Linear volume intersection
            old_deck.volume = max(0.0, old_deck.volume - (1.0 / steps))
            deck_fading.volume = min(1.0, deck_fading.volume + (1.0 / steps))
            
    # swap pointer and designations once crossfade finishes
    with audio_lock:
        old_deck.close()
        deck_active = deck_fading
        deck_fading = AudioDeck()
        playback_pointer = 0.0
        track_change_triggered = False
        cumulative_steps = 0
    print(">>> crossfade done")

def encoder_callback(channel):
    global wheel_velocity, last_encoder_time, is_scratching, cumulative_steps, last_step_time, track_change_triggered
    
    # ignore wheel gestures if paused
    if is_paused: return 
    
    now = time.time()
    dt = now - last_encoder_time
    
    if now - last_step_time > 0.40:
        cumulative_steps = 0
        
    clk_state = GPIO.input(CLK_PIN)
    dt_state = GPIO.input(DT_PIN)
    
    if dt > 0.001:  
        is_scratching = True
        speed = 0.02 / dt
        speed = min(speed, 3.5)  
        
        if clk_state != dt_state:
            wheel_velocity = speed      
            
            if not track_change_triggered: 
                cumulative_steps += 1       
        
        else:
            wheel_velocity = -speed     
            if not track_change_triggered: cumulative_steps -= 1       
            
        last_encoder_time = now
        last_step_time = now

def button_callback(channel):
    global is_paused, last_button_press
    now = time.time()
    if now - last_button_press > 0.30:
        is_paused = not is_paused
        
        if is_paused:
            print("\n>> paused")
        
        else:
            print("\n>> resumed")
        last_button_press = now

# hardware pins
GPIO.setmode(GPIO.BCM)
GPIO.setup(CLK_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(DT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# connect interrupt handler
GPIO.add_event_detect(CLK_PIN, GPIO.BOTH, callback=encoder_callback, bouncetime=1)
GPIO.add_event_detect(BUTTON_PIN, GPIO.FALLING, callback=button_callback, bouncetime=10)

print("\n~~~ GET DJ-ING! ~~~")

CHUNK_SIZE = 512

try:
    while True:
        now = time.time()
        
        # crossfade
        if not track_change_triggered and not is_paused:
            
            if abs(cumulative_steps) >= STEPS_PER_360:
                track_change_triggered = True
                
                if cumulative_steps > 0:
                    print("\n>>> next track...")
                    current_track_index = (current_track_index + 1) % len(PLAYLIST)
                
                else:
                    print("\n>>> prev track...")
                    current_track_index = (current_track_index - 1) % len(PLAYLIST)
                
                # launch crossfader on separate sub thread
                t = threading.Thread(target=crossfade_worker, args=(deck_active, PLAYLIST[current_track_index]))
                t.daemon = True
                t.start()

        if is_paused:
            time.sleep(0.05)
            continue

        # spin timeout
        if now - last_encoder_time > 0.20:
            is_scratching = False
            wheel_velocity = 1.0

        # build deck A chunk payload
        with audio_lock:
            if deck_active.raw_data is None:
                time.sleep(0.01)
                continue
                
            start_frame = int(playback_pointer)
            
            # switch between normal and scratch mode
            if not is_scratching:
                end_frame = start_frame + CHUNK_SIZE
                if end_frame >= deck_active.total_frames: 
                    end_frame = deck_active.total_frames - 1
                
                chunk_bytes = deck_active.raw_data[start_frame * deck_active.frame_size : end_frame * deck_active.frame_size]
                playback_pointer += CHUNK_SIZE
            
            else:
                if now - last_encoder_time > 0.05: 
                    wheel_velocity *= 0.7
                chunk_bytes = bytearray()
                
                for i in range(CHUNK_SIZE):
                    target_frame = int(playback_pointer + (i * wheel_velocity))
                    
                    if target_frame < 0: 
                        target_frame = 0
                    
                    elif target_frame >= deck_active.total_frames: 
                        target_frame = deck_active.total_frames - 1
                    
                    sb = target_frame * deck_active.frame_size
                    chunk_bytes.extend(deck_active.raw_data[sb : sb + deck_active.frame_size])
                
                playback_pointer += CHUNK_SIZE * wheel_velocity
                
                if playback_pointer < 0: 
                    playback_pointer = 0.0

            # submit hardware audio payload
            chunk_bytes = apply_volume_to_chunk(bytes(chunk_bytes), deck_active.volume)
            try:
                deck_active.process.stdin.write(chunk_bytes)
            except:
                pass

        # build deck B fade chunk payload if currently awake
        with audio_lock:
            if deck_fading.process and deck_fading.raw_data:
                # fade track plays forward from the start during the mix
                fade_start = int(time.time() * deck_fading.framerate) % (deck_fading.total_frames - CHUNK_SIZE)
                fade_chunk = deck_fading.raw_data[fade_start * deck_fading.frame_size : (fade_start + CHUNK_SIZE) * deck_fading.frame_size]
                fade_chunk = apply_volume_to_chunk(fade_chunk, deck_fading.volume)
                
                try:
                    deck_fading.process.stdin.write(fade_chunk)
                
                except:
                    pass

        # give CPU small rest to stay synced with audio time
        time.sleep(0.005)

except KeyboardInterrupt:
    print("\npowering down...")

finally:
    deck_active.close()
    deck_fading.close()
    GPIO.cleanup()
    print("cya later alligator")
