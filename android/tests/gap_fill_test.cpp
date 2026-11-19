// SPDX-License-Identifier: GPL-3.0-or-later
#include "prokrita_gap_fill.h"
#include <cstdlib>
#include <iostream>

int checks=0;
void check(bool value,const char *message){++checks;if(!value){std::cerr<<message<<'\n';std::exit(1);}}
std::vector<uint8_t> box(int gap,int side=0) {
    std::vector<uint8_t> pixels(80*80,255);
    for(int i=20;i<=60;++i){pixels[20*80+i]=0;pixels[60*80+i]=0;pixels[i*80+20]=0;pixels[i*80+60]=0;}
    for(int i=40-gap/2;i<40-gap/2+gap;++i) {
        if(side==0)pixels[20*80+i]=255;
        if(side==1)pixels[60*80+i]=255;
        if(side==2)pixels[i*80+20]=255;
        if(side==3)pixels[i*80+60]=255;
    }
    return pixels;
}
int main() {
    using namespace ProKrita;
    for(int side=0;side<4;++side)for(int gap=0;gap<=10;++gap) {
        auto source=box(gap,side);
        const auto original=source;
        auto result=closeTinyFillGaps(source,80,80,40,40,2);
        check(source==original,"source lineart changed");
        check(result.status==(gap<=4?GapFillStatus::Filled:GapFillStatus::Escaped),"wrong gap limit");
        if(result.status==GapFillStatus::Filled) {
            check(result.mask[40*80+40]==255,"interior missing");
            check(result.mask[5*80+5]==0,"outside flooded");
            for(size_t i=0;i<source.size();++i)check(source[i] || !result.mask[i],"lineart overwritten");
        }
        auto outside=closeTinyFillGaps(source,80,80,5,5,2);
        check(outside.status==GapFillStatus::Escaped,"outside drop accepted");
    }
    check(closeTinyFillGaps(box(1),80,80,40,40,0).status==GapFillStatus::Escaped,"disabled closure repaired a gap");
    check(closeTinyFillGaps(std::vector<uint8_t>(80*80,255),80,80,40,40,2).status==GapFillStatus::Escaped,"blank canvas filled");
    auto hole=box(3);hole[40*80+40]=0;
    auto result=closeTinyFillGaps(hole,80,80,45,45,2);
    check(result.status==GapFillStatus::Filled && result.mask[40*80+40]==0,"internal barrier lost");
    check(closeTinyFillGaps(box(2),80,80,21,21,2).status==GapFillStatus::SeedNearBoundary,"unsafe seed accepted");
    check(closeTinyFillGaps({},80,80,40,40,2).status==GapFillStatus::Invalid,"invalid input accepted");
    check(closeTinyFillGaps(box(2),80,80,-1,0,2).status==GapFillStatus::Invalid,"invalid seed accepted");
    std::cout<<checks<<" gap-fill assertions passed\n";
}
