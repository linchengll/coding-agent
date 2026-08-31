#include <cstddef>
#include <iostream>

template <typename T>
class Vector {
public:
    Vector() : data_(nullptr), size_(0), capacity_(0) {}

    ~Vector() {
        delete[] data_;
    }

    void push_back(const T& value) {
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

    std::size_t size() const {
        return size_;
    }

    T& operator[](std::size_t index) {
        return data_[index];
    }

    const T& operator[](std::size_t index) const {
        return data_[index];
    }

private:
    T* data_;
    std::size_t size_;
    std::size_t capacity_;
};

int main() {
    Vector<int> v;
    for (int i = 1; i <= 5; ++i) {
        v.push_back(i);
    }

    std::cout << "size=" << v.size() << std::endl;
    std::cout << "v[2]=" << v[2] << std::endl;
    return 0;
}
