#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int subtract(int a, int b) {
    return a - b;
}

int multiply(int a, int b) {
    return a * b;
}

/* 除法：除零防护，返回 0 并打印错误提示 */
int divide(int a, int b) {
    if (b == 0) {
        printf("错误：除数不能为 0\n");
        return 0;
    }
    return a / b;
}

int main(void) {
    int a = 3, b = 7;
    int c = 10, d = 2;

    printf("3 * 7 = %d\n", multiply(a, b));
    printf("10 / 2 = %d\n", divide(c, d));

    /* 顺便演示除零防护 */
    printf("10 / 0 = %d\n", divide(c, 0));

    return 0;
}
