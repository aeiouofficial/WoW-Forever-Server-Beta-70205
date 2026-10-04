#include "CreatureScale.h"
#include <iostream>
#include <limits>
#include <stdexcept>
int main()
{
    auto check = [](float actual, float expected, char const* name) {
        if (actual != expected) throw std::runtime_error(name);
        std::cout << "PASS " << name << '\n';
    };
    check(NormalizeCreatureScale(0.0f), 1.0f, "legacy zero scale cannot reach client");
    check(NormalizeCreatureScale(-1.0f), 1.0f, "negative scale cannot reach client");
    check(NormalizeCreatureScale(std::numeric_limits<float>::quiet_NaN()), 1.0f, "NaN cannot reach client");
    check(NormalizeCreatureScale(std::numeric_limits<float>::infinity()), 1.0f, "infinite scale cannot reach client");
    check(NormalizeCreatureScale(std::numeric_limits<float>::epsilon()), 1.0f, "client epsilon assertion boundary is rejected");
    check(NormalizeCreatureScale(0.5f), 0.5f, "intentional small scale is preserved");
    check(NormalizeCreatureScale(2.0f), 2.0f, "intentional large scale is preserved");
}
