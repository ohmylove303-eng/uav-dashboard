"""
🏙️ 건물 높이 예측 모듈
용적률(FAR) + 건폐율(BCR) + 용도지역 기반
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from enum import Enum


class ZoningType(str, Enum):
    """용도지역 분류"""
    RESIDENTIAL_1 = "제1종전용주거"      # 1종전용: 4층 이하
    RESIDENTIAL_2 = "제2종전용주거"      # 2종전용: 4층 이하
    RESIDENTIAL_GENERAL_1 = "제1종일반주거"  # 1종일반: 4층 이하
    RESIDENTIAL_GENERAL_2 = "제2종일반주거"  # 2종일반: 7-15층
    RESIDENTIAL_GENERAL_3 = "제3종일반주거"  # 3종일반: 15-25층
    SEMI_RESIDENTIAL = "준주거"          # 준주거: 10-20층
    COMMERCIAL_CENTRAL = "중심상업"      # 중심상업: 20-50층
    COMMERCIAL_GENERAL = "일반상업"      # 일반상업: 15-30층
    COMMERCIAL_NEIGHBOR = "근린상업"     # 근린상업: 10-20층
    INDUSTRIAL = "공업"                 # 공업: 5-15층
    GREEN = "녹지"                      # 녹지: 4층 이하


@dataclass
class ZoningSpec:
    """용도지역별 법적 기준"""
    max_far: float       # 최대 용적률 (%)
    max_bcr: float       # 최대 건폐율 (%)
    max_floors: int      # 최대 층수 (일반적)
    typical_floors: int  # 건물 footprint 정보가 없을 때 쓰는 대표 층수
    avg_floor_height: float = 3.3  # 평균 층고 (m)


# 서울시 용도지역별 기준 (출처: 서울시 도시계획조례)
ZONING_SPECS: Dict[ZoningType, ZoningSpec] = {
    ZoningType.RESIDENTIAL_1: ZoningSpec(max_far=100, max_bcr=50, max_floors=4, typical_floors=4),
    ZoningType.RESIDENTIAL_2: ZoningSpec(max_far=120, max_bcr=50, max_floors=4, typical_floors=4),
    ZoningType.RESIDENTIAL_GENERAL_1: ZoningSpec(max_far=150, max_bcr=60, max_floors=4, typical_floors=4),
    ZoningType.RESIDENTIAL_GENERAL_2: ZoningSpec(max_far=200, max_bcr=60, max_floors=15, typical_floors=12),
    ZoningType.RESIDENTIAL_GENERAL_3: ZoningSpec(max_far=300, max_bcr=50, max_floors=25, typical_floors=20),
    ZoningType.SEMI_RESIDENTIAL: ZoningSpec(max_far=400, max_bcr=60, max_floors=20, typical_floors=15),
    ZoningType.COMMERCIAL_CENTRAL: ZoningSpec(max_far=1000, max_bcr=80, max_floors=50, typical_floors=35),
    ZoningType.COMMERCIAL_GENERAL: ZoningSpec(max_far=800, max_bcr=80, max_floors=30, typical_floors=20),
    ZoningType.COMMERCIAL_NEIGHBOR: ZoningSpec(max_far=600, max_bcr=70, max_floors=20, typical_floors=12),
    ZoningType.INDUSTRIAL: ZoningSpec(max_far=300, max_bcr=60, max_floors=15, typical_floors=6),
    ZoningType.GREEN: ZoningSpec(max_far=80, max_bcr=20, max_floors=4, typical_floors=2),
}


@dataclass
class BuildingHeightResult:
    """건물 높이 예측 결과"""
    estimated_height: float        # 예측 높이 (m)
    estimated_floors: int          # 예측 층수
    max_possible_height: float     # 최대 가능 높이 (m)
    zoning_type: str              # 용도지역
    far_used: float               # 적용 용적률
    bcr_used: float               # 적용 건폐율
    confidence: float             # 신뢰도 (0-1)
    method: str                   # 예측 방법


class BuildingHeightPredictor:
    """건물 높이 예측기"""
    
    def __init__(self):
        self.floor_height = 3.3  # 기본 층고 (m)
    
    def predict_by_zoning(self, zoning: str, lot_area: float = 500, 
                         building_area: float = None) -> BuildingHeightResult:
        """
        용도지역 기반 높이 예측
        
        Args:
            zoning: 용도지역 (예: "제3종일반주거", "일반상업")
            lot_area: 대지면적 (㎡)
            building_area: 건축면적 (㎡), 미입력 시 추정
        """
        # 용도지역 매칭
        zoning_type = self._match_zoning(zoning)
        spec = ZONING_SPECS.get(zoning_type, ZONING_SPECS[ZoningType.RESIDENTIAL_GENERAL_2])
        
        # 좌표 기반처럼 건물 footprint 정보가 없으면 FAR/BCR 나눗셈 대신 대표 층수를 사용한다.
        # 단지형 아파트는 대지 전체 BCR과 동별 층수가 직접 비례하지 않아 3층처럼 과소추정되기 쉽다.
        if building_area is None:
            bcr = round(spec.max_bcr * 0.9, 1)
            estimated_floors = spec.typical_floors
            method = "zoning_typical"
        else:
            bcr = (building_area / lot_area) * 100 if lot_area > 0 else spec.max_bcr
            estimated_floors = min(
                max(1, int(spec.max_far / bcr)),
                spec.max_floors
            )
            method = "zoning_far_bcr"

        # 높이 계산
        estimated_height = estimated_floors * self.floor_height
        max_height = spec.max_floors * self.floor_height
        
        # 신뢰도 (용도지역 정확도 기반)
        confidence = 0.8 if zoning == zoning_type.value else 0.6
        if building_area is None:
            confidence -= 0.05
        
        return BuildingHeightResult(
            estimated_height=round(estimated_height, 1),
            estimated_floors=estimated_floors,
            max_possible_height=round(max_height, 1),
            zoning_type=zoning_type.value,
            far_used=spec.max_far,
            bcr_used=round(bcr, 1),
            confidence=confidence,
            method=method
        )
    
    def predict_by_far_bcr(self, far: float, bcr: float, 
                          floor_height: float = 3.3) -> BuildingHeightResult:
        """
        용적률/건폐율 직접 입력 기반 예측
        
        Args:
            far: 용적률 (%)
            bcr: 건폐율 (%)
            floor_height: 층고 (m)
        """
        estimated_floors = int(far / bcr) if bcr > 0 else 1
        estimated_height = estimated_floors * floor_height
        
        # 용도지역 역추정
        zoning_type = self._estimate_zoning_from_far(far)
        
        return BuildingHeightResult(
            estimated_height=round(estimated_height, 1),
            estimated_floors=estimated_floors,
            max_possible_height=round(estimated_height * 1.2, 1),
            zoning_type=zoning_type.value,
            far_used=far,
            bcr_used=bcr,
            confidence=0.9,  # 직접 입력은 높은 신뢰도
            method="far_bcr_input"
        )
    
    def predict_from_coordinates(self, lat: float, lon: float) -> BuildingHeightResult:
        """
        좌표 기반 높이 예측 (주변 환경 추정)
        
        Note: 실제로는 국토정보플랫폼 API 또는 건축물대장 조회 필요
        여기서는 위치 기반 휴리스틱 사용
        """
        # 서울 주요 상업지역 좌표 (간단 휴리스틱)
        commercial_areas = [
            (37.5665, 126.9780, "중심상업", "서울역"),      # 서울역
            (37.5024, 127.0246, "중심상업", "강남역"),      # 강남역
            (37.5562, 126.9373, "일반상업", "여의도"),      # 여의도
            (37.5663, 126.9785, "중심상업", "명동"),        # 명동
            (37.5138, 127.1000, "일반상업", "잠실"),        # 잠실
        ]
        
        # 가장 가까운 상업지역 찾기
        min_dist = float('inf')
        estimated_zoning = "제2종일반주거"  # 기본값
        area_name = "일반 주거지역"
        
        for clat, clon, zoning, name in commercial_areas:
            dist = ((lat - clat) ** 2 + (lon - clon) ** 2) ** 0.5
            if dist < min_dist and dist < 0.02:  # 약 2km 이내
                min_dist = dist
                estimated_zoning = zoning
                area_name = name
        
        result = self.predict_by_zoning(estimated_zoning)
        result.method = f"coordinate_based ({area_name})"
        result.confidence = max(0.4, 0.8 - min_dist * 20)  # 거리에 따라 신뢰도 감소
        
        return result
    
    def _match_zoning(self, zoning: str) -> ZoningType:
        """용도지역 문자열 매칭"""
        zoning = zoning.replace(" ", "")
        
        for zt in ZoningType:
            if zt.value in zoning or zoning in zt.value:
                return zt
        
        # 키워드 기반 매칭
        if "상업" in zoning:
            if "중심" in zoning:
                return ZoningType.COMMERCIAL_CENTRAL
            elif "근린" in zoning:
                return ZoningType.COMMERCIAL_NEIGHBOR
            return ZoningType.COMMERCIAL_GENERAL
        elif "주거" in zoning:
            if "3종" in zoning or "제3종" in zoning:
                return ZoningType.RESIDENTIAL_GENERAL_3
            elif "2종" in zoning or "제2종" in zoning:
                return ZoningType.RESIDENTIAL_GENERAL_2
            return ZoningType.RESIDENTIAL_GENERAL_1
        elif "공업" in zoning or "산업" in zoning:
            return ZoningType.INDUSTRIAL
        elif "녹지" in zoning:
            return ZoningType.GREEN
        
        return ZoningType.RESIDENTIAL_GENERAL_2  # 기본값
    
    def _estimate_zoning_from_far(self, far: float) -> ZoningType:
        """용적률에서 용도지역 역추정"""
        if far >= 800:
            return ZoningType.COMMERCIAL_CENTRAL
        elif far >= 500:
            return ZoningType.COMMERCIAL_GENERAL
        elif far >= 300:
            return ZoningType.RESIDENTIAL_GENERAL_3
        elif far >= 200:
            return ZoningType.RESIDENTIAL_GENERAL_2
        elif far >= 150:
            return ZoningType.RESIDENTIAL_GENERAL_1
        else:
            return ZoningType.RESIDENTIAL_1
    
    def get_surrounding_estimate(self, lat: float, lon: float, 
                                 radius: float = 100) -> Dict:
        """
        주변 건물 높이 분포 추정
        
        Args:
            lat, lon: 중심 좌표
            radius: 반경 (m)
        
        Returns:
            주변 건물 높이 통계
        """
        base_result = self.predict_from_coordinates(lat, lon)
        
        # 주변 건물 분포 추정 (±30%)
        h = base_result.estimated_height
        
        return {
            "center": {"lat": lat, "lon": lon},
            "radius_m": radius,
            "estimated_stats": {
                "min_height": round(h * 0.5, 1),
                "avg_height": round(h * 0.85, 1),
                "max_height": round(h * 1.2, 1),
                "dominant_floors": base_result.estimated_floors
            },
            "zoning": base_result.zoning_type,
            "confidence": round(base_result.confidence, 2)
        }


# ============================================
# FastAPI 엔드포인트용 함수
# ============================================

predictor = BuildingHeightPredictor()

def predict_building_height(lat: float, lon: float, 
                           zoning: str = None,
                           far: float = None,
                           bcr: float = None) -> Dict:
    """API용 래퍼 함수"""
    
    if far is not None and bcr is not None:
        result = predictor.predict_by_far_bcr(far, bcr)
    elif zoning:
        result = predictor.predict_by_zoning(zoning)
    else:
        result = predictor.predict_from_coordinates(lat, lon)
    
    return {
        "estimated_height_m": result.estimated_height,
        "estimated_floors": result.estimated_floors,
        "max_possible_height_m": result.max_possible_height,
        "zoning_type": result.zoning_type,
        "far_percent": result.far_used,
        "bcr_percent": result.bcr_used,
        "confidence": result.confidence,
        "method": result.method
    }


# ============================================
# 테스트
# ============================================

if __name__ == "__main__":
    print("🏙️ 건물 높이 예측 테스트\n")
    
    predictor = BuildingHeightPredictor()
    
    # 테스트 1: 용도지역 기반
    print("1. 용도지역 기반 예측:")
    result = predictor.predict_by_zoning("제3종일반주거", lot_area=500)
    print(f"   용도: {result.zoning_type}")
    print(f"   예측 높이: {result.estimated_height}m ({result.estimated_floors}층)")
    print(f"   최대 가능: {result.max_possible_height}m")
    print(f"   신뢰도: {result.confidence * 100:.0f}%")
    
    # 테스트 2: 용적률/건폐율 직접 입력
    print("\n2. 용적률/건폐율 기반 예측:")
    result = predictor.predict_by_far_bcr(far=800, bcr=80)
    print(f"   예측 높이: {result.estimated_height}m ({result.estimated_floors}층)")
    
    # 테스트 3: 좌표 기반
    print("\n3. 좌표 기반 예측 (강남역):")
    result = predictor.predict_from_coordinates(37.5024, 127.0246)
    print(f"   용도: {result.zoning_type}")
    print(f"   예측 높이: {result.estimated_height}m ({result.estimated_floors}층)")
    print(f"   신뢰도: {result.confidence * 100:.0f}%")
    
    # 테스트 4: 주변 분포
    print("\n4. 주변 건물 분포 (반경 100m):")
    stats = predictor.get_surrounding_estimate(37.5024, 127.0246)
    print(f"   최소: {stats['estimated_stats']['min_height']}m")
    print(f"   평균: {stats['estimated_stats']['avg_height']}m")
    print(f"   최대: {stats['estimated_stats']['max_height']}m")
