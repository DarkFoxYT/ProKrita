// SPDX-License-Identifier: GPL-3.0-or-later
#include "prokrita_geometry.h"
#include <cstdlib>
#include <iostream>
#include <string>
using namespace ProKrita;
int checks=0;
void require(bool pass,const std::string& message) {
    ++checks;if(!pass){std::cerr<<"FAIL: "<<message<<'\n';std::exit(1);}
}
std::vector<Point> ellipse(double radius,double ratio,double angle,double jitter,double direction=1.) {
    std::vector<Point> points;
    for(int i=0;i<=240;++i) {
        const double t=direction*2*pi*std::pow(i/240.,1.4),noise=1+jitter*std::sin(13*t);
        const double x=radius*std::cos(t)*noise,y=radius*ratio*std::sin(t)*noise;
        points.push_back({200+x*std::cos(angle)-y*std::sin(angle),200+x*std::sin(angle)+y*std::cos(angle)});
    }
    return points;
}
int main() {
    for(double radius:{16.,60.,180.})for(double angle:{0.,.47,1.2})
    for(double ratio:{1.,.84,.55})for(double jitter:{0.,.018})for(double direction:{-1.,1.}) {
        auto pts=ellipse(radius,ratio,angle,jitter,direction);const Shape result=fit(pts);
        require(result.kind==(ratio>=.84?Kind::Circle:Kind::Oval),"ellipse intent r="+std::to_string(radius)+" ratio="+std::to_string(ratio));
        const auto outlinePoints=outline(result,pts.front(),direction);
        require(distance(outlinePoints.front(),outlinePoints.back())<1e-8,"closed outline");
        double winding=0;for(size_t i=1;i<outlinePoints.size();++i)winding+=cross(outlinePoints[i-1]-center(result),outlinePoints[i]-center(result));
        require(winding*direction>0,"preserved winding");
        if(result.kind==Kind::Circle) {
            const double r=distance(result.controls[0],result.controls[1]);
            for(auto p:outlinePoints)require(std::abs(distance(p,result.controls[0])-r)<1e-8,"procedural circle radius");
            const Point c=center(result),anchor=pts.back(),u=anchor-c;
            const Shape resized=held(result,anchor,c+Point{-u.y*1.7,u.x*1.7});
            require(std::abs(distance(resized.controls[0],resized.controls[1])-r*1.7)<1e-7,"held circle scales");
            require(std::abs(distance(resized.controls[0],resized.controls[1])-distance(resized.controls[0],resized.controls[2]))<1e-8,"held circle stays round");
        }
        std::vector<Point> repeated;for(auto p:pts)for(int j=0;j<7;++j)repeated.push_back(p);
        require(fit(repeated).kind==result.kind,"duplicate tablet events do not change intent");
    }
    require(!fit({}),"empty input");require(!fit({{1,1}}),"one sample");
    require(!fit(std::vector<Point>(200,{1,1})),"stationary pen");
    require(!fit({{0,0},{std::numeric_limits<double>::quiet_NaN(),2},{10,10}}),"reject nonfinite samples");
    require(!fit({{0,0},{1,1},{2,2}}),"reject tiny gesture");
    require(fit({{0,0},{40,1},{90,0}}).kind==Kind::Line,"line");
    require(fit({{0,0},{100,0},{50,80},{0,0}}).kind==Kind::Triangle,"triangle");
    require(fit({{0,0},{100,0},{100,80},{0,80},{0,0}}).kind==Kind::Quadrilateral,"quadrilateral");
    require(fit({{0,0},{50,0},{50,60},{110,60}}).kind==Kind::Polyline,"cornered polyline");
    std::vector<Point> arcPoints;
    for(int i=0;i<=100;++i){const double t=pi*i/100;arcPoints.push_back({100+70*std::cos(t),100+70*std::sin(t)});}
    require(fit(arcPoints).kind==Kind::Arc,"semicircle arc");
    const auto arcOutline=outline(fit(arcPoints));
    require(distance(arcOutline.front(),arcPoints.front())<1e-7&&distance(arcOutline.back(),arcPoints.back())<1e-7,"arc endpoints");
    const auto steeredArc=held(fit(arcPoints),arcPoints.back(),arcPoints.back()+Point{25,40});
    require(distance(outline(steeredArc).front(),arcPoints.front())<1e-7,"steering an arc preserves its start");
    // Pure core must remain bounded even with huge input event streams.
    auto dense=ellipse(160,.88,.5,.015);std::vector<Point> many;
    for(int i=0;i<100;++i)many.insert(many.end(),dense.begin(),dense.end());
    const auto result=fit(many);require(!result||outline(result).size()<=8192,"bounded procedural output");
    std::cout<<"PASS: "<<checks<<" native geometry assertions\n";
}
