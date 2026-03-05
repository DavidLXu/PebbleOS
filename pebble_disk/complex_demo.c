int main() {
    // staged arithmetic pipeline
    int seed = 17;
    int a = seed * 3 + 5;
    int b = a * a - 9;
    int c = b / 4 + a * 2;

    // emulate a small hash-style mix (unrolled)
    int mix1 = c * 31 + 7;
    int mix2 = mix1 * 17 + b;
    int mix3 = mix2 * 13 + a;
    int checksum = mix3 + seed + c;

    // score breakdown
    int score_math = a + b + c;
    int score_mix = mix1 + mix2 + mix3;
    int final_score = score_math + score_mix + checksum;

    printf("%d\n", a);
    printf("%d\n", b);
    printf("%d\n", c);
    printf("%d\n", checksum);
    printf("%d\n", final_score);
    return final_score;
}
