#include "test_util.h"
#include "vram.h"

void test_vram_estimate_follows_the_measurements(void)
{
    /* measured (V2-7, with the video): 370 / 850 / 1460 / 2220 MiB for 1 / 4 / 8 / 12 places: the estimate must not be below them */
    CHECK(vram_needed_mib(1, 1) >= 370);
    CHECK(vram_needed_mib(4, 1) >= 850);
    CHECK(vram_needed_mib(8, 1) >= 1460);
    CHECK(vram_needed_mib(12, 1) >= 2220);
    /* ... and not absurdly above (the margin is 15 %, and the line is straight between the points) */
    CHECK(vram_needed_mib(12, 1) < 2220 * 14 / 10);
    CHECK(vram_needed_mib(1, 1) < 370 * 20 / 10);
}

void test_vram_estimate_grows_with_places_and_with_the_video(void)
{
    for (unsigned n = 1; n < 32; n++) {
        CHECK(vram_needed_mib(n + 1, 0) > vram_needed_mib(n, 0));
        CHECK(vram_needed_mib(n + 1, 1) > vram_needed_mib(n, 1));
        CHECK(vram_needed_mib(n, 1) > vram_needed_mib(n, 0));
    }
    CHECK(vram_needed_mib(0, 0) == vram_needed_mib(1, 0));   /* no places is read as one */
}

void test_vram_check_refuses_what_cannot_fit(void)
{
    /* the case that crashed in V2-7: 12 places with the video and about 120 MiB to spare after everything else */
    CHECK(!vram_is_enough(2300, 12, 1));
    CHECK(vram_is_enough(vram_needed_mib(12, 1), 12, 1));    /* exactly enough is enough */
    CHECK(vram_is_enough(8000, 12, 1));
    CHECK(!vram_is_enough(0, 1, 0));
    CHECK(!vram_is_enough(-1, 1, 0));
}
