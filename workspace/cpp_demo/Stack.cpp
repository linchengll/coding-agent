#include <cstddef>
#include <iostream>

template <typename T>
class Stack {
public:
    Stack() : data_(nullptr), size_(0), capacity_(0) {}

    ~Stack() {
        delete[] data_;
    }

    // 入栈：向栈顶压入一个元素
    void push(const T& value) {
        if (size_ == capacity_) {
            std::size_t new_capacity = capacity_ == 0 ? 1 : capacity_ * 2;
            T* new_data = new T[new_capacity];
            for (std::size_t i = 0; i < size_; ++i) {
                new_data[i] = data_[i];
            }
            delete[] data_;
            data_ = new_data;
            capacity_ = new_capacity;
        }
        data_[size_] = value;
        ++size_;
    }

    // 出栈：移除栈顶元素
    void pop() {
        if (size_ == 0) {
            return;
        }
        --size_;
    }

    // 返回栈顶元素（只读）
    const T& top() const {
        return data_[size_ - 1];
    }

    // 返回栈顶元素（可修改）
    T& top() {
        return data_[size_ - 1];
    }

    // 判断栈是否为空
    bool empty() const {
        return size_ == 0;
    }

    // 返回栈中元素个数
    std::size_t size() const {
        return size_;
    }

private:
    T* data_;
    std::size_t size_;
    std::size_t capacity_;
};

int main() {
    Stack<int> s;

    for (int i = 1; i <= 5; ++i) {
        s.push(i);
    }

    std::cout << "size=" << s.size() << std::endl;
    std::cout << "top=" << s.top() << std::endl;

    s.pop();
    std::cout << "after pop, size=" << s.size() << ", top=" << s.top() << std::endl;

    while (!s.empty()) {
        s.pop();
    }
    std::cout << "after clear, empty=" << (s.empty() ? "true" : "false") << std::endl;

    return 0;
}
