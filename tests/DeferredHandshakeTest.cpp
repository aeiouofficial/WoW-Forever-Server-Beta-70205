#include "DeferredHandshake.h"
#include <chrono>
#include <iostream>
#include <stdexcept>

int main()
{
    using namespace std::chrono_literals;
    using Clock = DeferredHandshake::Clock;
    auto start = Clock::time_point{};
    auto check = [](bool value, char const* name) {
        if (!value) throw std::runtime_error(name);
        std::cout << "PASS " << name << '\n';
    };
    DeferredHandshake gate;
    check(!gate.Consume(start), "unarmed handshake never sends");
    gate.Arm(5000ms, start);
    check(!gate.Consume(start + 4999ms), "configured delay prevents premature identity packet");
    check(gate.Consume(start + 5000ms), "identity packet sends at deadline");
    check(!gate.Consume(start + 6000ms), "identity packet sends exactly once");
    gate.Arm(0ms, start);
    check(gate.Consume(start), "zero delay preserves upstream behavior");
    gate.Arm(5000ms, start);
    gate.Cancel();
    check(!gate.Consume(start + 6000ms), "closed connection cancels pending handshake");
    gate.Arm(5000ms, start);
    gate.Arm(5000ms, start + 1000ms);
    check(!gate.Consume(start + 5000ms), "rearming replaces old deadline");
    check(gate.Consume(start + 6000ms), "replacement deadline sends once");
}
