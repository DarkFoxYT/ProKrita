// SPDX-License-Identifier: GPL-3.0-or-later
// No Qt/Python dependency: the same recognizer can be tested on a host compiler.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <limits>
#include <vector>

namespace ProKrita {
constexpr double pi = 3.14159265358979323846;
struct Point { double x = 0, y = 0; };
inline Point operator+(Point a, Point b) { return {a.x+b.x, a.y+b.y}; }
inline Point operator-(Point a, Point b) { return {a.x-b.x, a.y-b.y}; }
inline Point operator*(Point a, double b) { return {a.x*b, a.y*b}; }
inline double dot(Point a, Point b) { return a.x*b.x+a.y*b.y; }
inline double cross(Point a, Point b) { return a.x*b.y-a.y*b.x; }
inline double norm(Point a) { return std::hypot(a.x,a.y); }
inline double distance(Point a, Point b) { return norm(a-b); }
enum class Kind { None, Line, Arc, Circle, Oval, Triangle, Quadrilateral, Polyline, Curve };
struct Shape {
    Kind kind = Kind::None;
    // Ellipses: center and two axis endpoints. Arcs: center, start, sweep in radians.
    std::vector<Point> controls;
    double sweep = 0;
    bool closed() const { return kind==Kind::Circle || kind==Kind::Oval || kind==Kind::Triangle || kind==Kind::Quadrilateral; }
    explicit operator bool() const { return kind != Kind::None; }
};
inline std::vector<double> lengths(const std::vector<Point>& pts) {
    std::vector<double> result(pts.size(),0.);
    for (size_t i=1;i<pts.size();++i) result[i]=result[i-1]+distance(pts[i-1],pts[i]);
    return result;
}
inline Point sample(const std::vector<Point>& pts, const std::vector<double>& stops, double at) {
    if (pts.size()<2) return pts.empty()?Point{}:pts.front();
    const auto found=std::upper_bound(stops.begin(),stops.end(),at);
    const size_t i=std::min(pts.size()-2,size_t(std::max<std::ptrdiff_t>(0,found-stops.begin()-1)));
    const double t=std::clamp((at-stops[i])/std::max(1e-12,stops[i+1]-stops[i]),0.,1.);
    return pts[i]+(pts[i+1]-pts[i])*t;
}
inline double segmentError(Point p, Point a, Point b) {
    const Point d=b-a;
    const double t=std::clamp(dot(p-a,d)/std::max(1e-12,dot(d,d)),0.,1.);
    return distance(p,a+d*t);
}
inline void simplifyRange(const std::vector<Point>& pts,size_t first,size_t last,double tol,std::vector<Point>& out) {
    double error=0.; size_t split=first;
    for(size_t i=first+1;i<last;++i) {
        const double e=segmentError(pts[i],pts[first],pts[last]);
        if(e>error) { error=e;split=i; }
    }
    if(error>tol) { simplifyRange(pts,first,split,tol,out); simplifyRange(pts,split,last,tol,out); }
    else out.push_back(pts[first]);
}
inline std::vector<Point> simplify(const std::vector<Point>& pts,double tol) {
    if(pts.size()<3) return pts;
    std::vector<Point> out; simplifyRange(pts,0,pts.size()-1,tol,out); out.push_back(pts.back()); return out;
}
inline Shape oval(const std::vector<Point>& loop) {
    const size_t n=loop.size()-1;
    Point center{}; for(size_t i=0;i<n;++i) center=center+loop[i]; center=center*(1./n);
    double xx=0,yy=0,xy=0;
    for(size_t i=0;i<n;++i) { const Point p=loop[i]-center; xx+=p.x*p.x; yy+=p.y*p.y; xy+=p.x*p.y; }
    const double angle=std::atan2(2*xy,xx-yy)/2, c=std::cos(angle),s=std::sin(angle);
    std::vector<Point> uv; double l=1e100,r=-1e100,t=1e100,b=-1e100;
    for(size_t i=0;i<n;++i) {
        const Point p=loop[i]-center, q{c*p.x+s*p.y,-s*p.x+c*p.y}; uv.push_back(q);
        l=std::min(l,q.x);r=std::max(r,q.x);t=std::min(t,q.y);b=std::max(b,q.y);
    }
    const Point mid{(l+r)/2,(t+b)/2}; double rx=(r-l)/2,ry=(b-t)/2;
    if(std::min(rx,ry)<3) return {};
    double ovalError=0,maxError=0,radius=0; std::vector<double> residuals;
    for(auto p:uv) {
        p=p-mid; const double e=std::hypot(p.x/rx,p.y/ry)-1;
        residuals.push_back(e); ovalError+=std::abs(e);maxError=std::max(maxError,std::abs(e));radius+=norm(p);
    }
    ovalError/=n;radius/=n;
    if(maxError>.32 || ovalError>.107) return {};
    double wobble=0,circleError=0;
    for(size_t i=0;i<n;++i) {
        wobble+=std::abs(residuals[i]-residuals[(i+1)%n]);
        circleError+=std::abs(norm(uv[i]-mid)-radius);
    }
    wobble/=n;circleError/=n*radius;
    const bool circle=std::min(rx,ry)/std::max(rx,ry)>=.76 && circleError<=ovalError+.065+std::min(.025,wobble*.7);
    center=center+Point{c*mid.x-s*mid.y,s*mid.x+c*mid.y};
    if(circle) return {Kind::Circle,{center,center+Point{radius,0},center+Point{0,radius}}};
    return {Kind::Oval,{center,center+Point{c*rx,s*rx},center+Point{-s*ry,c*ry}}};
}
inline Shape arc(const std::vector<Point>& pts,double scale,double total) {
    const Point a=pts.front(),b=pts[pts.size()/2],e=pts.back(),u=b-a,v=e-a;
    const double det=2*cross(u,v); if(std::abs(det)<1e-7) return {};
    const Point center=a+Point{(v.y*dot(u,u)-u.y*dot(v,v))/det,(u.x*dot(v,v)-v.x*dot(u,u))/det};
    const double radius=distance(a,center);double mean=0,max=0,sweep=0;
    for(size_t i=0;i<pts.size();++i) {
        const double err=std::abs(distance(pts[i],center)-radius);mean+=err;max=std::max(max,err);
        if(i) { const Point p=pts[i-1]-center,q=pts[i]-center;sweep+=std::atan2(cross(p,q),dot(p,q)); }
    }
    if(max>std::max(2.8,.058*scale) || mean/pts.size()>std::max(1.2,.024*scale) ||
       std::abs(sweep)<=.34 || std::abs(sweep)>=1.94*pi || std::abs(sweep)*radius<total*.86) return {};
    return {Kind::Arc,{center,a},sweep};
}
inline Shape fit(const std::vector<Point>& raw) {
    std::vector<Point> pts;
    for(auto p:raw) {
        if(!std::isfinite(p.x)||!std::isfinite(p.y)) return {};
        if(pts.empty()||distance(pts.back(),p)>1e-7) pts.push_back(p);
    }
    if(pts.size()<2) return {};
    double l=pts[0].x,r=l,t=pts[0].y,b=t;
    for(auto p:pts) { l=std::min(l,p.x);r=std::max(r,p.x);t=std::min(t,p.y);b=std::max(b,p.y); }
    const double scale=std::hypot(r-l,b-t);if(scale<12) return {};
    const auto stops=lengths(pts);const double total=stops.back();
    std::vector<Point> path;for(int i=0;i<=96;++i) path.push_back(sample(pts,stops,total*i/96));
    const Point a=pts.front(),end=pts.back();const double chord=distance(a,end);
    if(chord>=.74*scale && total<=1.35*chord) {
        double dev=0;for(auto p:path) dev=std::max(dev,segmentError(p,a,end));
        if(dev<std::max(2.1,.076*scale)) {
            if(pts.size()>=8 && dev>std::max(2.5,.032*scale)) { auto result=arc(path,scale,total);if(result)return result; }
            return {Kind::Line,{a,end}};
        }
    }
    if(chord<=.19*scale && total>1.7*scale) {
        size_t far=0;for(size_t i=1;i<path.size();++i)if(distance(a,path[i])>distance(a,path[far]))far=i;
        std::vector<Point> loop(path.begin()+far,path.end());loop.insert(loop.end(),path.begin(),path.begin()+far);loop.push_back(path[far]);
        size_t across=0;for(size_t i=1;i<loop.size();++i)if(distance(loop[0],loop[i])>distance(loop[0],loop[across]))across=i;
        const double tol=std::max(2.,scale*.042);
        auto first=simplify(std::vector<Point>(loop.begin(),loop.begin()+across+1),tol);
        auto second=simplify(std::vector<Point>(loop.begin()+across,loop.end()),tol);
        first.pop_back();second.pop_back();first.insert(first.end(),second.begin(),second.end());
        bool valid=first.size()>=3&&first.size()<=4;
        for(size_t i=0;i<first.size()&&valid;++i)valid=distance(first[i],first[(i+first.size()-1)%first.size()])>.13*scale;
        if(valid)return {first.size()==3?Kind::Triangle:Kind::Quadrilateral,first};
        return oval(path);
    }
    auto coarse=simplify(path,std::max(2.2,std::min(scale*.032,8.)));
    bool corners=coarse.size()>2&&coarse.size()<=12;
    for(size_t i=1;i+1<coarse.size()&&corners;++i) {
        size_t j=0;for(size_t k=1;k<path.size();++k)if(distance(path[k],coarse[i])<distance(path[j],coarse[i]))j=k;
        if(j<2||j+2>=path.size()) { corners=false;break; }
        const auto turn=[&](size_t span) {const Point u=coarse[i]-path[j-span],v=path[j+span]-coarse[i];return std::abs(std::atan2(cross(u,v),dot(u,v)));};
        const double near=turn(2),wide=turn(std::min<size_t>(7,std::min(j,path.size()-1-j)));
        corners=near>.39&&near>.65*wide;
    }
    if(corners)return {Kind::Polyline,coarse};
    auto curved=arc(path,scale,total);if(curved)return curved;
    auto knots=simplify(path,std::max(1.7,std::min(6.,scale*.019)));
    if(pts.size()>=4&&total>24&&knots.size()>=3&&knots.size()<=28)return {Kind::Curve,knots};
    return {};
}
inline Point center(const Shape& shape) {
    if(shape.kind==Kind::Circle||shape.kind==Kind::Oval||shape.kind==Kind::Arc)return shape.controls.front();
    Point p{};for(auto q:shape.controls)p=p+q;return p*(1./std::max<size_t>(1,shape.controls.size()));
}
inline Shape held(const Shape& source,Point anchor,Point point) {
    Shape result=source;
    if(source.kind==Kind::Line) {result.controls.back()=point;return result;}
    const Point origin=source.closed()?center(source):
        (source.kind==Kind::Arc?source.controls[1]:source.controls.front());
    const Point u=anchor-origin,v=point-origin;
    const double denom=dot(u,u);if(denom<1||norm(v)<2)return source;
    const double re=dot(v,u)/denom,im=cross(u,v)/denom;
    for(auto& p:result.controls){const Point q=p-origin;p=origin+Point{re*q.x-im*q.y,im*q.x+re*q.y};}
    return result;
}
// Screen-space tessellation bounds preview/recognition work on high-DPI tablets.
inline std::vector<Point> outline(const Shape& shape,Point start={},double winding=1.) {
    if(!shape)return {};
    const auto& pts=shape.controls;
    if(shape.kind==Kind::Circle||shape.kind==Kind::Oval||shape.kind==Kind::Arc) {
        const Point c=pts[0],u=pts[1]-c;
        const Point v=shape.kind==Kind::Arc?Point{-u.y,u.x}:pts[2]-c;
        double first=0,sweep=shape.kind==Kind::Arc?shape.sweep:(winding>=0?2*pi:-2*pi);
        if(shape.kind!=Kind::Arc)first=std::atan2(dot(start-c,v)/dot(v,v),dot(start-c,u)/dot(u,u));
        const double radius=std::max(norm(u),norm(v));
        const int count=std::clamp(int(std::ceil(std::abs(sweep)*std::sqrt(std::max(1.,radius)/.8))),24,2048);
        std::vector<Point> out;for(int i=0;i<=count;++i){const double a=first+sweep*i/count;out.push_back(c+u*std::cos(a)+v*std::sin(a));}
        if(shape.closed())out.back()=out.front();
        return out;
    }
    if(shape.kind==Kind::Curve) {
        std::vector<Point> out{pts[0]};
        for(size_t i=0;i+1<pts.size();++i) {
            const Point a=pts[i],b=pts[i+1],before=pts[i?i-1:0],after=pts[std::min(i+2,pts.size()-1)];
            const double chord=distance(a,b);
            const Point u=a+(b-before)*(chord/(3*std::max(.001,distance(a,before)+chord)));
            const Point v=b-(after-a)*(chord/(3*std::max(.001,distance(after,b)+chord)));
            const int count=std::clamp(int(std::ceil((distance(a,u)+distance(u,v)+distance(v,b))/2)),2,256);
            for(int j=1;j<=count;++j){const double t=double(j)/count,s=1-t;out.push_back(a*(s*s*s)+u*(3*s*s*t)+v*(3*s*t*t)+b*(t*t*t));}
        }return out;
    }
    auto out=pts;
    if(shape.closed()) {
        const auto closest=std::min_element(out.begin(),out.end(),[&](Point a,Point b){return distance(a,start)<distance(b,start);});
        std::rotate(out.begin(),closest,out.end());out.push_back(out.front());
    }
    return out;
}
} // namespace ProKrita
