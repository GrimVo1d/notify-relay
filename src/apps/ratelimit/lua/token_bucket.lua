-- Atomic token bucket.
--
-- KEYS[1]  -- hash key holding {tokens, last_refill_ms}
-- ARGV[1]  -- rate (tokens per second, float)
-- ARGV[2]  -- burst (max tokens, integer)
-- ARGV[3]  -- now (ms, integer)
-- ARGV[4]  -- requested tokens (integer)
--
-- Returns: {allowed (0|1), tokens_remaining (string), retry_after_ms (integer)}
local rate     = tonumber(ARGV[1])
local burst    = tonumber(ARGV[2])
local now_ms   = tonumber(ARGV[3])
local req      = tonumber(ARGV[4])

local data = redis.call('HMGET', KEYS[1], 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last   = tonumber(data[2])

if tokens == nil then tokens = burst end
if last   == nil then last   = now_ms end

local elapsed_ms = now_ms - last
if elapsed_ms < 0 then elapsed_ms = 0 end

local refill = (elapsed_ms * rate) / 1000.0
tokens = tokens + refill
if tokens > burst then tokens = burst end

local allowed = 0
local retry_after_ms = 0
if tokens >= req then
    tokens = tokens - req
    allowed = 1
else
    local need = req - tokens
    retry_after_ms = math.ceil((need * 1000.0) / rate)
end

redis.call('HMSET', KEYS[1], 'tokens', tostring(tokens), 'last_refill', tostring(now_ms))
local ttl_s = math.ceil(burst / rate) + 1
redis.call('EXPIRE', KEYS[1], ttl_s)

return {allowed, tostring(tokens), retry_after_ms}
