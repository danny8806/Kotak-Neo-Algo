"""Shared sliding-window broker pacing; wait only on HTTP worker threads."""
from collections import deque
import threading
import time


class SlidingWindowLimiter:
    def __init__(self, capacity, period=1.0, *, clock=time.monotonic, sleep=time.sleep):
        self.capacity=capacity
        self.period=period
        self.clock=clock
        self.sleep=sleep
        self.lock=threading.Lock()
        self.requests=deque()

    def acquire(self):
        while True:
            with self.lock:
                now=self.clock()
                while self.requests and self.requests[0]<=now-self.period:
                    self.requests.popleft()
                if len(self.requests)<self.capacity:
                    self.requests.append(now)
                    return
                delay=self.requests[0]+self.period-now+.001
            self.sleep(max(.001,delay))
