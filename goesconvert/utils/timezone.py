from datetime import tzinfo
from datetime import timedelta


class Zone(tzinfo):
    def __init__(self, offset, isdst, name):
        self.offset = offset
        self.isdst = isdst
        self.name = name

    def utcoffset(self, dt):
        return timedelta(hours=self.offset) + self.dst(dt)

    def dst(self, dt):
            return timedelta(hours=1) if self.isdst else timedelta(0)

    def tzname(self,dt):
         return self.name


GMT = Zone(0, False, 'GMT')
EST = Zone(-5, False, 'EST')
PST = Zone(-8, False, 'PST')
