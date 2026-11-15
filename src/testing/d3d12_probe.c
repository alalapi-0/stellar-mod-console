/* Self-authored, fixed offscreen Windows ABI experiment; no CRT/SDK/assets.
 * ABI facts: Microsoft DirectX-Headers adbd6f3 and documented DXGI base ABI.
 * Build with GCC ms_abi and GNU ld i386pep. No command-line configuration.
 */
#include <stdint.h>
#include <stddef.h>
#define MS __attribute__((ms_abi))
typedef int32_t HR;
typedef uint32_t U;
typedef uint64_t Q;
typedef void *Handle;
typedef struct { U a; uint16_t b,c; uint8_t d[8]; } Guid;
typedef struct { uint16_t description[128]; U vendor,device,subsystem,revision;
    Q video,system,shared; U luid_low; int32_t luid_high; U flags; } AdapterDesc;
typedef struct { U type; int32_t priority; U flags,node; } QueueDesc;
typedef struct { U type,cpu,pool,creation,visible; } HeapProperties;
typedef struct { U dimension; Q alignment,width; U height; uint16_t depth,mips;
    U format,sample_count,sample_quality,layout,flags; } ResourceDesc;
typedef struct { U type,count,flags,node; } DescriptorDesc;
typedef struct { Q ptr; } CpuHandle;
typedef struct { U format,width,height,depth,pitch; } Footprint;
typedef struct { Q offset; Footprint footprint; } Placed;
typedef struct { Handle resource; U type; union { Placed placed; U subresource; } u; } CopyLocation;
typedef struct { U type,flags; Handle resource; U subresource,before,after; } Barrier;
typedef struct { Q begin,end; } Range;
_Static_assert(sizeof(Guid)==16 && sizeof(AdapterDesc)==312, "Windows DXGI ABI");
_Static_assert(offsetof(AdapterDesc,vendor)==256 && offsetof(AdapterDesc,flags)==304, "DXGI offsets");
_Static_assert(sizeof(ResourceDesc)==56 && sizeof(HeapProperties)==20, "D3D12 resource ABI");
_Static_assert(sizeof(CopyLocation)==48 && offsetof(CopyLocation,u)==16, "Copy ABI");
_Static_assert(sizeof(Barrier)==32 && sizeof(Placed)==32, "Transition/footprint ABI");
#define IMPORT(ret,name,...) extern ret (MS *__imp_##name)(__VA_ARGS__)
IMPORT(Handle,GetStdHandle,U);
IMPORT(int32_t,WriteFile,Handle,const void *,U,U *,void *);
IMPORT(void,ExitProcess,U);
IMPORT(Handle,LoadLibraryExA,const char *,Handle,U);
IMPORT(void *,GetProcAddress,Handle,const char *);
IMPORT(U,GetLastError,void);
IMPORT(Handle,CreateEventA,void *,int32_t,int32_t,const char *);
IMPORT(U,WaitForSingleObject,Handle,U);
IMPORT(int32_t,CloseHandle,Handle);
IMPORT(Handle,CreateFileA,const char *,U,U,void *,U,U,Handle);
#define IID(name,a,b,c,x0,x1,x2,x3,x4,x5,x6,x7) \
    static const Guid name={a,b,c,{x0,x1,x2,x3,x4,x5,x6,x7}}
IID(factory_iid,0x770aae78,0xf26f,0x4dba,0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87);
IID(device_iid,0x189819f1,0x1db6,0x4b57,0xbe,0x54,0x18,0x21,0x33,0x9b,0x85,0xf7);
IID(queue_iid,0x0ec870a6,0x5d7e,0x4c22,0x8c,0xfc,0x5b,0xaa,0xe0,0x76,0x16,0xed);
IID(allocator_iid,0x6102dee4,0xaf59,0x4b09,0xb9,0x99,0xb4,0x4d,0x73,0xf0,0x9b,0x24);
IID(list_iid,0x5b160d0f,0xac1b,0x4185,0x8b,0xa8,0xb3,0xae,0x42,0xa5,0xa4,0x55);
IID(resource_iid,0x696442be,0xa72e,0x4059,0xbc,0x79,0x5b,0x5c,0x98,0x04,0x0f,0xad);
IID(fence_iid,0x0a753dcf,0xc4d8,0x4b91,0xad,0xf6,0xbe,0x5a,0x60,0xd9,0x5a,0x76);
IID(heap_iid,0x8efb471d,0x616c,0x4f49,0x90,0xf7,0x12,0x7b,0xb7,0x63,0xfa,0x51);
#define CALL(obj,slot,ret,...) ((ret (MS *)(Handle,##__VA_ARGS__))(*(void ***)obj)[slot])
static void release(Handle p) { if(p) CALL(p,2,U)(p); }
static void print(const char *s) { U n=0,w=0; while(s[n]) n++; __imp_WriteFile(__imp_GetStdHandle((U)-11),s,n,&w,0); }
static void number(Q v) { char b[24]; U n=0,w=0; do { b[n++]=(char)('0'+v%10); v/=10; } while(v);
    for(U i=0;i<n/2;i++){ char c=b[i]; b[i]=b[n-1-i]; b[n-1-i]=c; }
    __imp_WriteFile(__imp_GetStdHandle((U)-11),b,n,&w,0); }
static uint8_t pixels[4096];
__attribute__((ms_abi,noreturn)) void entry(void) {
    Handle factory=0,adapter=0,device=0,queue=0,allocator=0,list=0,texture=0,buffer=0,heap=0,fence=0,event=0,file=0;
    U stage=1,last_error=0,written=0,rows=0; HR hr=0; int passed=0,mapped=0; Q row_bytes=0,total=0;
    AdapterDesc desc={0}; Placed placed={0}; uint8_t *data=0;
    Handle dxgi=__imp_LoadLibraryExA("dxgi.dll",0,0x800), d3d=__imp_LoadLibraryExA("d3d12.dll",0,0x800);
    if(!dxgi || !d3d) goto cleanup;
    HR (MS *create_factory)(const Guid *,Handle *)=__imp_GetProcAddress(dxgi,"CreateDXGIFactory1");
    HR (MS *create_device)(Handle,U,const Guid *,Handle *)=__imp_GetProcAddress(d3d,"D3D12CreateDevice");
    if(!create_factory || !create_device) goto cleanup;
#define CHECK(n,expr) do { stage=n; hr=(expr); if(hr<0) goto cleanup; } while(0)
    CHECK(2,create_factory(&factory_iid,&factory)); if(!factory) goto cleanup;
    for(U i=0;i<8;i++) {
        hr=CALL(factory,12,HR,U,Handle *)(factory,i,&adapter);
        if(hr<0 || !adapter) break;
        CHECK(3,CALL(adapter,10,HR,AdapterDesc *)(adapter,&desc));
        if(desc.vendor==0x10de && !(desc.flags&2)) break;
        release(adapter); adapter=0;
    }
    stage=4; if(!adapter || desc.vendor!=0x10de || (desc.flags&2)) goto cleanup;
    CHECK(5,create_device(adapter,0xc000,&device_iid,&device)); if(!device) goto cleanup;
    QueueDesc q={0,0,0,0};
    CHECK(6,CALL(device,8,HR,const QueueDesc *,const Guid *,Handle *)(device,&q,&queue_iid,&queue));
    CHECK(7,CALL(device,9,HR,U,const Guid *,Handle *)(device,0,&allocator_iid,&allocator));
    CHECK(8,CALL(device,12,HR,U,U,Handle,Handle,const Guid *,Handle *)(device,0,0,allocator,0,&list_iid,&list));
    HeapProperties normal={1,0,0,1,1}, readback={3,0,0,1,1};
    ResourceDesc tex={3,0,32,32,1,1,28,1,0,0,1};
    CHECK(9,CALL(device,27,HR,const HeapProperties *,U,const ResourceDesc *,U,const void *,const Guid *,Handle *)
        (device,&normal,0,&tex,4,0,&resource_iid,&texture));
    stage=10; CALL(device,38,void,const ResourceDesc *,U,U,Q,Placed *,U *,Q *,Q *)
        (device,&tex,0,1,0,&placed,&rows,&row_bytes,&total);
    if(rows!=32 || row_bytes!=128 || total>8192 || total<4096 || placed.offset!=0 ||
       placed.footprint.format!=28 || placed.footprint.width!=32 || placed.footprint.height!=32 ||
       placed.footprint.depth!=1 || placed.footprint.pitch<128 || placed.footprint.pitch%256 ||
       31*(Q)placed.footprint.pitch+128>total) goto cleanup;
    ResourceDesc buf={1,0,total,1,1,1,0,1,0,1,0};
    CHECK(11,CALL(device,27,HR,const HeapProperties *,U,const ResourceDesc *,U,const void *,const Guid *,Handle *)
        (device,&readback,0,&buf,0x400,0,&resource_iid,&buffer));
    DescriptorDesc hd={2,1,0,0};
    CHECK(12,CALL(device,14,HR,const DescriptorDesc *,const Guid *,Handle *)(device,&hd,&heap_iid,&heap));
    /* Windows COM ABI returns this structure through an explicit output pointer. */
    CpuHandle cpu={0};
    CALL(heap,9,CpuHandle *,CpuHandle *)(heap,&cpu);
    if(!cpu.ptr) goto cleanup;
    CALL(device,20,void,Handle,const void *,CpuHandle)(device,texture,0,cpu);
    float color[4]={16.0f/255.0f,32.0f/255.0f,64.0f/255.0f,1.0f};
    CALL(list,48,void,CpuHandle,const float *,U,const void *)(list,cpu,color,0,0);
    Barrier transition={0,0,texture,0xffffffff,4,0x800};
    CALL(list,26,void,U,const Barrier *)(list,1,&transition);
    CopyLocation dst={buffer,1,{.placed=placed}}, src={texture,0,{.subresource=0}};
    CALL(list,16,void,const CopyLocation *,U,U,U,const CopyLocation *,const void *)(list,&dst,0,0,0,&src,0);
    CHECK(13,CALL(list,9,HR)(list));
    CHECK(14,CALL(device,36,HR,Q,U,const Guid *,Handle *)(device,0,0,&fence_iid,&fence));
    CALL(queue,10,void,U,const Handle *)(queue,1,&list);
    CHECK(15,CALL(queue,14,HR,Handle,Q)(queue,fence,1));
    stage=16; event=__imp_CreateEventA(0,0,0,0); if(!event) goto cleanup;
    CHECK(17,CALL(fence,9,HR,Q,Handle)(fence,1,event));
    stage=18; if(__imp_WaitForSingleObject(event,2000)!=0 || CALL(fence,8,Q)(fence)<1) goto cleanup;
    Range range={0,total}; CHECK(19,CALL(buffer,8,HR,U,const Range *,void **)(buffer,0,&range,(void **)&data)); mapped=1;
    stage=20; if(!data) goto cleanup;
    for(U y=0;y<32;y++) for(U x=0;x<128;x++) {
        uint8_t expected[4]={16,32,64,255}; uint8_t v=data[y*placed.footprint.pitch+x];
        if(v!=expected[x%4]) goto cleanup;
        pixels[y*128+x]=v;
    }
    stage=21; file=__imp_CreateFileA("Z:\\work\\readback.rgba",0x40000000,0,0,1,0x80,0);
    if(file==(Handle)(uintptr_t)-1) { file=0; goto cleanup; }
    if(!__imp_WriteFile(file,pixels,4096,&written,0) || written!=4096) goto cleanup;
    passed=1; stage=22;
cleanup:
    last_error=__imp_GetLastError();
    if(mapped){ Range empty={0,0}; CALL(buffer,9,void,U,const Range *)(buffer,0,&empty); }
    if(file) __imp_CloseHandle(file);
    if(event) __imp_CloseHandle(event);
    release(fence); release(heap); release(buffer); release(texture); release(list);
    release(allocator); release(queue); release(device); release(adapter); release(factory);
    print("{\"passed\":"); print(passed?"true":"false"); print(",\"stage\":"); number(stage);
    print(",\"hresult_u32\":"); number((U)hr); print(",\"last_error\":"); number(last_error);
    print(",\"vendor_id\":"); number(desc.vendor); print(",\"device_id\":"); number(desc.device);
    print(",\"minimum_feature_level\":49152,\"row_pitch\":"); number(placed.footprint.pitch);
    print(",\"footprint_bytes\":"); number(total); print(",\"readback_bytes\":"); number(written); print("}\n");
    __imp_ExitProcess(passed?0:1); __builtin_unreachable();
}
