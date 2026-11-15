/* Fixed, self-authored native GPU prerequisite check; no window or game APIs. */
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SIDE 32
#define BYTES (SIDE * SIDE * 4)
#define LIMIT (16 * 1024 * 1024)
#define CALL(expr) do { VkResult r = (expr); if (r != VK_SUCCESS) { \
    fprintf(stderr, "%s returned %d\n", #expr, r); return 2; } } while (0)

static uint32_t memory_type(VkPhysicalDevice gpu, uint32_t bits, VkMemoryPropertyFlags flags) {
    VkPhysicalDeviceMemoryProperties memory;
    vkGetPhysicalDeviceMemoryProperties(gpu, &memory);
    for (uint32_t i = 0; i < memory.memoryTypeCount; ++i)
        if ((bits & (1u << i)) && (memory.memoryTypes[i].propertyFlags & flags) == flags) return i;
    return UINT32_MAX;
}

int main(int argc, char **argv) {
    (void)argv;
    if (argc != 1) return 2;
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "Stellar fixed headless prerequisite", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo instance_info = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app};
    VkInstance instance;
    CALL(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t count = 0;
    CALL(vkEnumeratePhysicalDevices(instance, &count, NULL));
    if (!count || count > 8) return 2;
    VkPhysicalDevice devices[8], gpu = VK_NULL_HANDLE;
    CALL(vkEnumeratePhysicalDevices(instance, &count, devices));
    VkPhysicalDeviceProperties properties = {0};
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties candidate;
        vkGetPhysicalDeviceProperties(devices[i], &candidate);
        if (candidate.vendorID == 0x10de && candidate.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) {
            if (gpu != VK_NULL_HANDLE) return 2;
            gpu = devices[i]; properties = candidate;
        }
    }
    if (gpu == VK_NULL_HANDLE || properties.apiVersion < VK_API_VERSION_1_1) return 2;
    VkFormatProperties format;
    vkGetPhysicalDeviceFormatProperties(gpu, VK_FORMAT_R8G8B8A8_UNORM, &format);
    VkFormatFeatureFlags required = VK_FORMAT_FEATURE_TRANSFER_SRC_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
    if ((format.optimalTilingFeatures & required) != required) return 2;
    uint32_t queue_count = 0, family = UINT32_MAX;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, NULL);
    if (!queue_count || queue_count > 64) return 2;
    VkQueueFamilyProperties queues[64];
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, queues);
    for (uint32_t i = 0; i < queue_count; ++i)
        if (queues[i].queueCount && (queues[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) { family = i; break; }
    if (family == UINT32_MAX) return 2;
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info};
    VkDevice device;
    CALL(vkCreateDevice(gpu, &device_info, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    VkImageCreateInfo image_info = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {SIDE, SIDE, 1}, .mipLevels = 1, .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_TRANSFER_SRC_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
    VkImage image;
    CALL(vkCreateImage(device, &image_info, NULL, &image));
    VkMemoryRequirements image_requirements;
    vkGetImageMemoryRequirements(device, image, &image_requirements);
    uint32_t image_type = memory_type(gpu, image_requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (image_type == UINT32_MAX || image_requirements.size > LIMIT) return 2;
    VkMemoryAllocateInfo allocation = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = image_requirements.size, .memoryTypeIndex = image_type};
    VkDeviceMemory image_memory;
    CALL(vkAllocateMemory(device, &allocation, NULL, &image_memory));
    CALL(vkBindImageMemory(device, image, image_memory, 0));
    VkBufferCreateInfo buffer_info = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = BYTES, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE};
    VkBuffer buffer;
    CALL(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
    VkMemoryRequirements buffer_requirements;
    vkGetBufferMemoryRequirements(device, buffer, &buffer_requirements);
    uint32_t buffer_type = memory_type(gpu, buffer_requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (buffer_type == UINT32_MAX || buffer_requirements.size > LIMIT) return 2;
    allocation.allocationSize = buffer_requirements.size; allocation.memoryTypeIndex = buffer_type;
    VkDeviceMemory buffer_memory;
    CALL(vkAllocateMemory(device, &allocation, NULL, &buffer_memory));
    CALL(vkBindBufferMemory(device, buffer, buffer_memory, 0));
    void *mapped;
    CALL(vkMapMemory(device, buffer_memory, 0, BYTES, 0, &mapped));
    memset(mapped, 0xa5, BYTES);
    VkCommandPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = family};
    VkCommandPool pool;
    CALL(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    VkCommandBufferAllocateInfo command_info = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool, .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1};
    VkCommandBuffer command;
    CALL(vkAllocateCommandBuffers(device, &command_info, &command));
    VkCommandBufferBeginInfo begin = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};
    CALL(vkBeginCommandBuffer(command, &begin));
    VkImageSubresourceRange range = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    VkImageMemoryBarrier image_barrier = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image, .subresourceRange = range};
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
        0, 0, NULL, 0, NULL, 1, &image_barrier);
    VkClearColorValue color = {.float32 = {16.0f / 255, 32.0f / 255, 64.0f / 255, 1.0f}};
    vkCmdClearColorImage(command, image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
    image_barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    image_barrier.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    image_barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    image_barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
        0, 0, NULL, 0, NULL, 1, &image_barrier);
    VkBufferImageCopy copy = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
        .imageExtent = {SIDE, SIDE, 1}};
    vkCmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
    VkBufferMemoryBarrier host_barrier = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = buffer, .offset = 0, .size = BYTES};
    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
        0, 0, NULL, 1, &host_barrier, 0, NULL);
    CALL(vkEndCommandBuffer(command));
    VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence;
    CALL(vkCreateFence(device, &fence_info, NULL, &fence));
    VkSubmitInfo submit = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1, .pCommandBuffers = &command};
    CALL(vkQueueSubmit(queue, 1, &submit, fence));
    CALL(vkWaitForFences(device, 1, &fence, VK_TRUE, 2000000000ULL));
    const unsigned char expected[4] = {16, 32, 64, 255};
    for (uint32_t i = 0; i < BYTES; ++i)
        if (((unsigned char *)mapped)[i] != expected[i % 4]) {
            fprintf(stderr, "readback mismatch at %u\n", i); return 3;
        }
    FILE *output = fopen("/work/readback.rgba", "wb");
    if (!output) return 2;
    int saved = fwrite(mapped, 1, BYTES, output) == BYTES;
    if (fclose(output) || !saved) return 2;
    printf("{\"vendor_id\":%u,\"device_id\":%u,\"device_type\":%u,\"api_version\":%u,"
           "\"driver_version\":%u,\"queue_family\":%u,\"bytes_checked\":%u,"
           "\"allocated_bytes\":%llu,\"exact_readback\":true}\n",
        properties.vendorID, properties.deviceID, properties.deviceType, properties.apiVersion,
        properties.driverVersion, family, BYTES,
        (unsigned long long)(image_requirements.size + buffer_requirements.size));
    vkUnmapMemory(device, buffer_memory);
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    vkDestroyBuffer(device, buffer, NULL); vkFreeMemory(device, buffer_memory, NULL);
    vkDestroyImage(device, image, NULL); vkFreeMemory(device, image_memory, NULL);
    vkDestroyDevice(device, NULL); vkDestroyInstance(instance, NULL);
    return 0;
}
