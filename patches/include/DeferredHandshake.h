#ifndef TRINITYCORE_DEFERRED_HANDSHAKE_H
#define TRINITYCORE_DEFERRED_HANDSHAKE_H

#include <chrono>
#include <optional>
#include <mutex>

// Deadline is consumed by the socket update thread; close can cancel elsewhere.
class DeferredHandshake
{
public:
    using Clock = std::chrono::steady_clock;
    void Arm(std::chrono::milliseconds delay, Clock::time_point now = Clock::now())
    {
        std::scoped_lock lock(_mutex);
        _deadline = now + delay;
    }
    void Cancel() { std::scoped_lock lock(_mutex); _deadline.reset(); }
    bool Consume(Clock::time_point now = Clock::now())
    {
        std::scoped_lock lock(_mutex);
        if (!_deadline || now < *_deadline)
            return false;
        _deadline.reset();
        return true;
    }
private:
    std::mutex _mutex;
    std::optional<Clock::time_point> _deadline;
};
#endif
