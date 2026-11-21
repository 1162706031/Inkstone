"""Redis-backed counters for login throttling and model admission."""

from redis import Redis

AUTH_RESERVE = """
for i=1,2 do
    if tonumber(redis.call('GET',KEYS[i]) or '0') >= tonumber(ARGV[i]) then return 0 end
end
for i=1,2 do
    local n=redis.call('INCR',KEYS[i])
    if n==1 then redis.call('PEXPIRE',KEYS[i],900000) end
end
return redis.call('PEXPIRETIME',KEYS[1]) .. ':' .. redis.call('PEXPIRETIME',KEYS[2])
"""

AUTH_RELEASE = """
for i=1,2 do
    if redis.call('PEXPIRETIME',KEYS[i]) == tonumber(ARGV[i]) then
        redis.call('DECR',KEYS[i])
    end
end
"""

ADMISSION = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', 180)
redis.call('SET', KEYS[2], ARGV[2], 'EX', 180)
redis.call('SET', KEYS[3], ARGV[3], 'EX', 180)
return tonumber(ARGV[1]) < tonumber(ARGV[4])
   and tonumber(ARGV[2]) < tonumber(ARGV[5])
   and tonumber(ARGV[3]) < tonumber(ARGV[6]) and 1 or 0
"""


def _client(url):
    return Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)


def reserve_auth_attempt(url, keys, *limits):
    """Count one attempt against each key; returns the key expiries, or ``None`` when a limit is reached."""
    with _client(url) as redis:
        result = redis.eval(AUTH_RESERVE, 2, *keys, *limits)
    return result.split(b":") if result else None


def release_auth_attempt(url, keys, expiries):
    with _client(url) as redis:
        redis.eval(AUTH_RELEASE, 2, *keys, *expiries)


def model_admission(url, keys, counts, limits):
    with _client(url) as redis:
        return bool(redis.eval(ADMISSION, 3, *keys, *counts, *limits))


def ping(url):
    with _client(url) as redis:
        return redis.ping()
