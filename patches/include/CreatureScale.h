#ifndef TRINITYCORE_CREATURE_SCALE_H
#define TRINITYCORE_CREATURE_SCALE_H
#include <cmath>
#include <limits>

// Older world databases use zero for default scale; modern clients reject it.
inline float NormalizeCreatureScale(float scale)
{
    return std::isfinite(scale) && scale > std::numeric_limits<float>::epsilon() ? scale : 1.0f;
}
#endif
