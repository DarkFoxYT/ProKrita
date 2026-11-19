// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <cstdint>
#include <cstddef>
#include <limits>
#include <vector>

namespace ProKrita {
enum class GapFillStatus { Filled, Escaped, SeedNearBoundary, Invalid };
struct GapFillResult {
    GapFillStatus status=GapFillStatus::Invalid;
    std::vector<uint8_t> mask;
};

// Separable binary morphology: O(width*height), independent of gap radius.
// Erosion locks the image edge so an outside fill cannot become a fake island.
inline std::vector<uint8_t> gapMorph(const std::vector<uint8_t> &input,int w,int h,int radius,bool grow)
{
    std::vector<uint8_t> horizontal(input.size()),output(input.size());
    const int span=2*radius+1;
    auto selected=[grow,span](int count){return uint8_t((grow?count>0:count==span)?255:0);};
    for(int y=0;y<h;++y) {
        auto at=[&](int x){return x<0 || x>=w ? int(!grow) : int(input[size_t(y)*w+x]!=0);};
        int count=0;for(int x=-radius;x<=radius;++x)count+=at(x);
        for(int x=0;x<w;++x){horizontal[size_t(y)*w+x]=selected(count);count+=at(x+radius+1)-at(x-radius);}
    }
    for(int x=0;x<w;++x) {
        auto at=[&](int y){return y<0 || y>=h ? int(!grow) : int(horizontal[size_t(y)*w+x]!=0);};
        int count=0;for(int y=-radius;y<=radius;++y)count+=at(y);
        for(int y=0;y<h;++y){output[size_t(y)*w+x]=selected(count);count+=at(y+radius+1)-at(y-radius);}
    }
    return output;
}

// Close narrow passages in fillable space, flood, then restore its boundary.
// The lineart is never edited. Large openings and outside drops still escape.
inline GapFillResult closeTinyFillGaps(const std::vector<uint8_t> &fillable,int w,int h,int sx,int sy,int radius)
{
    GapFillResult result;
    if(w<=0 || h<=0 || sx<0 || sy<0 || sx>=w || sy>=h || radius<0 || radius>6 ||
       uint64_t(w)*uint64_t(h)!=fillable.size() || fillable.size()>std::numeric_limits<uint32_t>::max())return result;
    auto narrow=radius ? gapMorph(fillable,w,h,radius,false) : fillable;
    const size_t seed=size_t(sy)*w+sx;
    if(!narrow[seed]){result.status=GapFillStatus::SeedNearBoundary;return result;}
    std::vector<uint8_t> region(fillable.size(),0);
    std::vector<uint32_t> queue;queue.push_back(uint32_t(seed));region[seed]=255;
    unsigned edges=0;
    auto visit=[&](uint32_t index){if(narrow[index] && !region[index]){region[index]=255;queue.push_back(index);}};
    for(size_t head=0;head<queue.size();++head) {
        const uint32_t index=queue[head];const int x=index%w,y=index/w;
        if(x==0)edges|=1;
        if(x==w-1)edges|=2;
        if(y==0)edges|=4;
        if(y==h-1)edges|=8;
        if(edges==15){result.status=GapFillStatus::Escaped;return result;}
        if(x>0)visit(index-1);
        if(x+1<w)visit(index+1);
        if(y>0)visit(index-w);
        if(y+1<h)visit(index+w);
    }
    if(radius)region=gapMorph(region,w,h,radius,true);
    for(size_t i=0;i<region.size();++i)region[i]=region[i] && fillable[i] ? 255 : 0;
    result.status=GapFillStatus::Filled;result.mask=std::move(region);return result;
}
}
